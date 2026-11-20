#!/usr/bin/env python3
"""Contour lines for one map area, from the GSI optimal vector tiles.

    python3 -I tool/contours.py maps/nishinomiya.json

Writes build/maps/<id>.contours.pmtiles with only the layers the manifest's
"contours" section names (Cntr: contour lines, height in vt_alti;
Cstline: coastline), cut to the area. build_map.py calls this too.

The GSI file is marked 検証中 (being tested): its address or layers may
change. The manifest records the file's date ("file_date", from the
server's Last-Modified); the build stops when the server has another date,
so a changed file is looked at before it ships.

Needs the `pmtiles` command (brew install pmtiles). The tile filter is
plain Python: it copies the wanted layers of each vector tile byte for
byte, so their features and attributes stay exactly as GSI made them.
"""
import email.utils
import gzip
import json
import sqlite3
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "map-cache"


# --- Protocol buffers (only what MVT needs) --------------------------------

def varint(buf, i):
    """Reads a varint at i; returns (value, next index)."""
    value = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return value, i


def encode_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def fields(buf):
    """Yields (field number, wire type, value, raw bytes of the whole field)."""
    i = 0
    while i < len(buf):
        start = i
        key, i = varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            n, i = varint(buf, i)
            value, i = buf[i:i + n], i + n
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if i > len(buf):
            raise ValueError("truncated field")
        yield num, wire, value, buf[start:i]


def layer_name(layer):
    for num, wire, value, _ in fields(layer):
        if num == 1 and wire == 2:
            return bytes(value).decode("utf-8")
    return None


def keep_layers(tile, keep):
    """An MVT tile (not compressed) with only the layers named in keep.
    Layers are field 3 of the tile; everything else is dropped too."""
    out = bytearray()
    for num, wire, value, raw in fields(tile):
        if num == 3 and wire == 2 and layer_name(value) in keep:
            out += raw
    return bytes(out)


# --- PMTiles v3 (reading a local file) -------------------------------------

def tile_id_to_zxy(tile_id):
    """PMTiles tile id (Hilbert order per zoom) → (z, x, y)."""
    acc, z = 0, 0
    while True:
        n = 4 ** z
        if tile_id < acc + n:
            break
        acc += n
        z += 1
    t, size = tile_id - acc, 2 ** z
    x = y = 0
    s = 1
    while s < size:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return z, x, y


def _directory(buf):
    buf = gzip.decompress(buf)
    n, i = varint(buf, 0)
    ids, last = [], 0
    for _ in range(n):
        v, i = varint(buf, i)
        last += v
        ids.append(last)
    run_lengths, lengths, offsets = [], [], []
    for _ in range(n):
        v, i = varint(buf, i)
        run_lengths.append(v)
    for _ in range(n):
        v, i = varint(buf, i)
        lengths.append(v)
    for k in range(n):
        v, i = varint(buf, i)
        offsets.append(offsets[-1] + lengths[k - 1] if v == 0 and k > 0 else v - 1)
    return list(zip(ids, run_lengths, offsets, lengths))


def read_pmtiles(path):
    """Yields ((z, x, y), tile bytes as stored) for every tile, and checks
    that directories and tiles are gzip-compressed vector tiles."""
    data = Path(path).read_bytes()
    if data[:7] != b"PMTiles" or data[7] != 3:
        raise ValueError(f"{path}: not a PMTiles v3 file")
    (root_off, root_len, _, _, leaf_off, _, tile_off, _) = struct.unpack("<8Q", data[8:72])
    internal_comp, tile_comp, tile_type = data[97], data[98], data[99]
    if internal_comp != 2 or tile_comp != 2 or tile_type != 1:
        raise ValueError(f"{path}: expected gzip-compressed MVT tiles")

    def walk(off, length):
        for tile_id, run, offset, size in _directory(data[off:off + length]):
            if run == 0:
                yield from walk(leaf_off + offset, size)
            else:
                tile = data[tile_off + offset:tile_off + offset + size]
                for r in range(run):
                    yield tile_id_to_zxy(tile_id + r), tile

    yield from walk(root_off, root_len)


def read_metadata(path):
    data = Path(path).read_bytes()
    _, _, meta_off, meta_len = struct.unpack("<4Q", data[8:40])
    return json.loads(gzip.decompress(data[meta_off:meta_off + meta_len]))


# --- Build -----------------------------------------------------------------

def server_date(url):
    """Last-Modified of the remote file as YYYY-MM-DD."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "lychnion-map-builder"})
    with urllib.request.urlopen(req, timeout=60) as r:
        modified = r.headers.get("Last-Modified")
    if not modified:
        sys.exit(f"{url}: no Last-Modified header")
    return email.utils.parsedate_to_datetime(modified).date().isoformat()


def filter_to_mbtiles(src, out, keep, bbox, center, attribution):
    """Writes an MBTiles file with only the kept layers of src. Tiles left
    empty are dropped. Returns (tile count, min zoom, max zoom)."""
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE metadata(name TEXT, value TEXT);
        CREATE TABLE tiles(zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
        CREATE UNIQUE INDEX tile_index ON tiles(zoom_level, tile_column, tile_row);
    """)
    count, zooms = 0, set()
    for (z, x, y), stored in read_pmtiles(src):
        kept = keep_layers(gzip.decompress(stored), keep)
        if not kept:
            continue
        # MBTiles counts rows from the bottom (TMS).
        db.execute("INSERT INTO tiles VALUES(?,?,?,?)",
                   (z, x, 2 ** z - 1 - y, gzip.compress(kept, 9, mtime=0)))
        count += 1
        zooms.add(z)
    if not count:
        db.close()
        sys.exit(f"no {', '.join(sorted(keep))} data in the area")
    layers = [l for l in read_metadata(src).get("vector_layers", []) if l.get("id") in keep]
    meta = {
        "name": "contours", "format": "pbf", "type": "overlay",
        "minzoom": str(min(zooms)), "maxzoom": str(max(zooms)),
        "bounds": ",".join(str(v) for v in bbox),
        # MBTiles: lon,lat,zoom. The manifest's center is [lat, lon].
        "center": f"{center[1]},{center[0]},{max(zooms)}",
        "attribution": attribution,
        "json": json.dumps({"vector_layers": layers}, ensure_ascii=False),
    }
    db.executemany("INSERT INTO metadata VALUES(?,?)", meta.items())
    db.commit()
    db.close()
    return count, min(zooms), max(zooms)


def build(m):
    """Builds build/maps/<id>.contours.pmtiles for manifest m. Returns its path."""
    c = m["contours"]
    date = server_date(c["source"])
    if date != c["file_date"]:
        sys.exit(f"the GSI file changed ({c['file_date']} in the manifest, {date} on the server): "
                 "check its layers and licence, then update \"file_date\" in the manifest")
    CACHE.mkdir(parents=True, exist_ok=True)
    out = ROOT / "build" / "maps" / f"{m['id']}.contours.pmtiles"
    out.parent.mkdir(parents=True, exist_ok=True)
    full = CACHE / f"{m['id']}.gsi-bvmap-{date}.pmtiles"
    if not full.exists():
        part = full.with_suffix(".part")
        part.unlink(missing_ok=True)
        subprocess.run(["pmtiles", "extract", c["source"], str(part),
                        "--bbox=" + ",".join(str(v) for v in m["bbox"]),
                        f"--maxzoom={m['maxzoom']}"], check=True)
        part.rename(full)
    mbtiles = CACHE / f"{m['id']}.contours.mbtiles"
    count, zmin, zmax = filter_to_mbtiles(full, mbtiles, set(c["layers"]), m["bbox"],
                                          m["center"], c["attribution"])
    out.unlink(missing_ok=True)
    subprocess.run(["pmtiles", "convert", str(mbtiles), str(out)], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["pmtiles", "verify", str(out)], check=True, stdout=subprocess.DEVNULL)
    mbtiles.unlink()
    print(f"contours: {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} MB, "
          f"{count} tiles, z{zmin}-{zmax}, GSI file of {date})")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if "contours" not in manifest:
        sys.exit(f"{sys.argv[1]} has no \"contours\" section")
    build(manifest)
