#!/usr/bin/env python3
"""Builds the offline walking graph for one map area.

    python3 -m venv build/route-venv
    build/route-venv/bin/pip install osmium pillow pyshp shapely
    build/route-venv/bin/python -I tool/build_routes.py maps/nishinomiya.json

Reads the OpenStreetMap extract named in the manifest ("routes.source",
downloaded once to build/map-cache/), keeps the ways people can walk on
inside the area's bbox, adds the ground height of every point from the GSI
elevation tiles (標高タイル, 5 m grid where available, else 10 m) and the
expected tsunami inundation depth (国土数値情報 津波浸水想定, A40, files named
in "routes.inundation"), and writes build/maps/<id>.routes.bin (not in git).

File format (little endian, read by lib/map/route_graph.dart):

    header   'FKRG', u32 version=4, u32 nodes, u32 edges
    lat      i32[nodes]   degrees * 1e6
    lon      i32[nodes]
    offsets  u32[nodes+1] edges of node i are offsets[i] .. offsets[i+1]-1
    targets  u32[edges]
    lengths  u16[edges]   decimetres
    (padding to 4 bytes)
    height   i16[nodes]   ground height above sea level, decimetres;
                          -32768 where the tiles have no value
    (padding to 4 bytes)
    flood    u8[nodes]    tsunami inundation: 0 outside and at least 50 m
                          from any inundation area, 1 outside but closer,
                          else inside: 16 * hi + lo, the expected depth
                          between DEPTHS[lo] and DEPTHS[hi] metres
                          (hi = 9: no upper bound)
    (padding to 4 bytes)

Version 1 (no heights), 2 (no flood) and 3 (flood as classes 2..8: <0.3 m,
0.3-1, 1-2, 2-3, 3-4, 4-5, 5 m or more) files are still read by the app.

Every edge is stored in both directions (one-way streets do not apply to
people on foot). Nodes are sorted by a 0.001° grid, so each grid cell is
one run of nodes. Small pieces of road not connected to the rest (under
MIN_PART nodes) are dropped, so a position never snaps onto them.
"""
import io
import json
import math
import re
import struct
import sys
import urllib.error
import urllib.request
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import osmium
import shapefile
import shapely
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "map-cache"
OUT = ROOT / "build" / "maps"

# Ways people can walk on. Motorways and their ramps are left out.
WALK = {
    "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "pedestrian", "footway",
    "path", "steps", "track", "cycleway", "road", "bridleway",
}
NO = {"no", "private"}
MIN_PART = 200


def fetch(url, name):
    path = CACHE / name
    if not path.exists():
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "lychnion-route-builder"})
        with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    return path


# GSI elevation tiles: 5 m grid (DEM5A, laser survey) first, 10 m where it
# has no value. https://maps.gsi.go.jp/development/demtile.html
DEM = [("dem5a_png", 15), ("dem_png", 14)]
NO_HEIGHT = -32768


def _tile_xy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _get_tile(name, z, x, y):
    """Pixels of one tile, or None if GSI has none (sea, outside the survey)."""
    path = CACHE / "dem" / name / str(z) / str(x) / f"{y}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://cyberjapandata.gsi.go.jp/xyz/{name}/{z}/{x}/{y}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "lychnion-route-builder"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                path.write_bytes(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            path.write_bytes(b"")  # remember: no tile here
    data = path.read_bytes()
    return Image.open(io.BytesIO(data)).convert("RGB").load() if data else None


class Heights:
    """Ground height at a point from the GSI tiles (metres, or None)."""

    def __init__(self):
        self.tiles = {}

    def preload(self, points):
        need = set()
        for lat, lon in points:
            for name, z in DEM:
                x, y = _tile_xy(lat, lon, z)
                need.add((name, z, int(x), int(y)))
        missing = [k for k in need if k not in self.tiles]
        print(f"{len(missing)} elevation tiles")
        # A few at a time: GSI is a public service.
        with ThreadPoolExecutor(4) as ex:
            for k, px in zip(missing, ex.map(lambda k: _get_tile(*k), missing)):
                self.tiles[k] = px

    def at(self, lat, lon):
        for name, z in DEM:
            x, y = _tile_xy(lat, lon, z)
            px = self.tiles.get((name, z, int(x), int(y)))
            if px is None:
                continue
            r, g, b = px[min(255, int((x % 1) * 256)), min(255, int((y % 1) * 256))]
            v = (r << 16) + (g << 8) + b
            if v == 1 << 23:
                continue  # no value at this pixel
            return (v - (1 << 24) if v > 1 << 23 else v) * 0.01
        return None


# A40 depth labels differ by prefecture and year ("0.3m未満", "0.01～0.3m",
# "1m以上 ～ 2m未満", "1.0～2.0m"): classify by the lower bound.
# Depth bounds in metres. Prefectures use different ranges (Hyogo 3-4 m,
# Wakayama 3-5 m and 10-20 m), so each area keeps its lower and upper bound
# as indexes into this list; 9 means no upper bound.
DEPTHS = [0, 0.3, 1, 2, 3, 4, 5, 10, 20]
NEAR_ZONE = 0.0005  # degrees, about 50 m


def depth_class(label):
    """(lo, hi) indexes into DEPTHS for an A40 depth label: "0.3m未満",
    "0.01～0.3m", "1m以上 ～ 2m未満", "1.0～2.0m", "5m以上", "5.0m～"."""
    s = label.replace("ｍ", "m").replace(" ", "").replace("～", "~")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if "未満" in s and "以上" not in s and "~" not in s:  # "0.3m未満"
        lo, hi = 0.0, nums[0]
    elif len(nums) >= 2:
        lo, hi = nums[0], nums[1]
    else:  # "5m以上", "5.0m～"
        lo, hi = nums[0], None
    lo_i = max(i for i, b in enumerate(DEPTHS) if b <= lo + 1e-9)
    if hi is None:
        return lo_i, 9
    hi_i = min((i for i, b in enumerate(DEPTHS) if b >= hi - 1e-9), default=9)
    if lo_i == 0 and hi_i == 0:
        hi_i = 1
    return lo_i, max(hi_i, lo_i + 1)


def inundation(files, bbox):
    """(polygons, depth class per polygon) inside the bbox."""
    w, s_, e, n = bbox
    area = shapely.box(w, s_, e, n)
    polys, classes = [], []
    for f in files:
        path = CACHE / "a40" / f
        r = shapefile.Reader(str(path), encoding="cp932")
        label_at = [x[0] for x in r.fields[1:]].index("A40_003")
        for sr in r.iterShapeRecords():
            b = sr.shape.bbox
            if b[2] < w or b[0] > e or b[3] < s_ or b[1] > n:
                continue
            g = shapely.geometry.shape(sr.shape.__geo_interface__)
            if not g.is_valid:
                g = shapely.make_valid(g)
            polys.append(g.intersection(area))
            classes.append(depth_class(sr.record[label_at]))
    return polys, classes


def flood_classes(points, files, bbox):
    """flood value (see the format) for each (lat, lon)."""
    polys, classes = inundation(files, bbox)
    print(f"{len(polys)} inundation polygons in the area")
    tree = shapely.STRtree(polys)
    geoms = shapely.points([(lon, lat) for lat, lon in points])
    out = bytearray(len(points))
    near_p, _ = tree.query(geoms, predicate="dwithin", distance=NEAR_ZONE)
    for i in near_p:
        out[i] = 1
    deepest = {}
    pi, poly = tree.query(geoms, predicate="intersects")
    for i, j in zip(pi, poly):
        if classes[j] > deepest.get(i, (-1, -1)):
            deepest[i] = classes[j]
    for i, (lo, hi) in deepest.items():
        out[i] = 16 * hi + lo
    return out


def walkable(tags):
    hw = tags.get("highway")
    if hw not in WALK:
        return False
    if tags.get("foot") in NO or tags.get("motorroad") == "yes":
        return False
    if tags.get("access") in NO and tags.get("foot") not in {"yes", "designated", "permissive"}:
        return False
    if tags.get("area") == "yes" and hw != "pedestrian":
        return False
    return True


def dist(a, b):
    """Metres between two (lat, lon) points (haversine)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


class Ways(osmium.SimpleHandler):
    def __init__(self, bbox):
        super().__init__()
        self.w, self.s, self.e, self.n = bbox
        self.loc = {}      # osm node id -> (lat, lon)
        self.pairs = set()  # undirected (a, b) osm node ids, a < b

    def way(self, w):
        if not walkable(w.tags):
            return
        prev = None
        for nd in w.nodes:
            if not nd.location.valid():
                prev = None
                continue
            lat, lon = nd.location.lat, nd.location.lon
            inside = self.s <= lat <= self.n and self.w <= lon <= self.e
            if not inside:
                prev = None
                continue
            self.loc[nd.ref] = (lat, lon)
            if prev is not None and prev != nd.ref:
                self.pairs.add((min(prev, nd.ref), max(prev, nd.ref)))
            prev = nd.ref


def parts(n, adj):
    """Connected parts as lists of node indexes."""
    seen = bytearray(n)
    out = []
    for start in range(n):
        if seen[start]:
            continue
        seen[start] = 1
        stack, part = [start], []
        while stack:
            v = stack.pop()
            part.append(v)
            for u in adj[v]:
                if not seen[u]:
                    seen[u] = 1
                    stack.append(u)
        out.append(part)
    return out


def main(manifest_path):
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    src = m["routes"]["source"]
    pbf = fetch(src, src.rsplit("/", 1)[1])

    h = Ways(m["bbox"])
    h.apply_file(str(pbf), locations=True, idx="flex_mem")
    print(f"{len(h.loc)} nodes, {len(h.pairs)} segments in bbox")

    ids = list(h.loc)
    index = {osm: i for i, osm in enumerate(ids)}
    adj = [[] for _ in ids]
    for a, b in h.pairs:
        ia, ib = index[a], index[b]
        adj[ia].append(ib)
        adj[ib].append(ia)

    keep = [p for p in parts(len(ids), adj) if len(p) >= MIN_PART]
    kept = sorted(v for p in keep for v in p)
    print(f"kept {len(kept)} nodes in {len(keep)} parts (dropped {len(ids) - len(kept)})")

    # Sort by a 0.001° grid (about 110 x 90 m), row by row: each grid cell
    # is one run of nodes, which the app uses to find the nearest node.
    def cell(v):
        lat, lon = h.loc[ids[v]]
        return (round(lat * 1e6) // 1000, round(lon * 1e6) // 1000)

    kept.sort(key=cell)
    new = {v: i for i, v in enumerate(kept)}

    lat = array("i", (round(h.loc[ids[v]][0] * 1e6) for v in kept))
    lon = array("i", (round(h.loc[ids[v]][1] * 1e6) for v in kept))
    hs = Heights()
    hs.preload(h.loc[ids[v]] for v in kept)
    height = array("h")
    for v in kept:
        e = hs.at(*h.loc[ids[v]])
        height.append(NO_HEIGHT if e is None else max(-32767, min(32767, round(e * 10))))
    unknown = sum(1 for e in height if e == NO_HEIGHT)
    print(f"heights: {len(kept) - unknown} points, {unknown} without a value")
    flood = array("B", flood_classes(
        [h.loc[ids[v]] for v in kept], m["routes"].get("inundation", []), m["bbox"]))
    inside = sum(1 for x in flood if x >= 16)
    print(f"inundation: {inside} points inside, {sum(1 for x in flood if x == 1)} near the edge")
    offsets, targets, lengths = array("I", [0]), array("I"), array("H")
    for v in kept:
        p = h.loc[ids[v]]
        for u in sorted(adj[v], key=lambda u: new[u]):
            targets.append(new[u])
            lengths.append(min(65535, max(1, round(dist(p, h.loc[ids[u]]) * 10))))
        offsets.append(len(targets))

    out = OUT / f"{m['id']}.routes.bin"
    with open(out, "wb") as f:
        f.write(b"FKRG" + struct.pack("<III", 4, len(kept), len(targets)))
        for a in (lat, lon, offsets, targets, lengths, height, flood):
            if sys.byteorder != "little":
                a.byteswap()
            f.write(a.tobytes())
            f.write(b"\0" * (-f.tell() % 4))
    km = sum(lengths) / 10 / 1000 / 2
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(kept)} nodes, {km:.0f} km of ways)")


if __name__ == "__main__":
    main(sys.argv[1])
