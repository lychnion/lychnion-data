#!/usr/bin/env python3
"""Builds the offline map data for one area from a JSON manifest.

    python3 -I tool/build_map.py maps/nishinomiya.json

Writes:
  build/maps/<id>.sites.sqlite    evacuation sites in the area (GSI data);
                                  the app bundles the areas without
                                  "bundle": false (copy them to its
                                  assets/maps/)
                                  meta "area": the regions its sites are
                                  in (or the manifest's "area")
  build/maps/<id>.pmtiles         base map (OpenStreetMap via Protomaps; not in git)

Needs the `pmtiles` command (brew install pmtiles). The GSI data comes with
usage notes that must reach the user; they are stored in the SQLite file and
shown by the app.
"""
import csv
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "map-cache"

# CSV column → hazard key used by the app. Order matches the GSI CSV.
HAZARDS = {
    "洪水": "flood",
    "崖崩れ、土石流及び地滑り": "landslide",
    "高潮": "storm_surge",
    "地震": "earthquake",
    "津波": "tsunami",
    "大規模な火事": "fire",
    "内水氾濫": "inland_flood",
    "火山現象": "volcano",
}


def fetch(url, name):
    path = CACHE / name
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "lychnion-map-builder"})
        with urllib.request.urlopen(req, timeout=120) as r:
            path.write_bytes(r.read())
    return path


def read_csv(path):
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))


def inside(bbox, lat, lon):
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def main(manifest_path):
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    CACHE.mkdir(parents=True, exist_ok=True)
    bbox, sites = m["bbox"], m["sites"]

    notice_raw = fetch(sites["notice_url"], "gsi-notice.txt").read_bytes()
    notice = next(notice_raw.decode(enc) for enc in ("utf-8", "cp932") if _decodes(notice_raw, enc))

    rows = []
    for pref in sites["prefectures"]:
        # _2: emergency evacuation sites (per hazard), _1: shelters (for staying).
        for kind, suffix in (("emergency", "_2"), ("shelter", "_1")):
            for r in read_csv(fetch(f"{sites['base_url']}{pref}{suffix}.csv", f"{pref}{suffix}.csv")):
                try:
                    lat, lon = float(r["緯度"]), float(r["経度"])
                except (KeyError, ValueError):
                    continue
                if not inside(bbox, lat, lon):
                    continue
                hazards = [key for col, key in HAZARDS.items() if kind == "emergency" and r.get(col) == "1"]
                rows.append((r["共通ID"], kind, r["施設・場所名"].strip(), r["住所"].strip(),
                             lat, lon, ",".join(hazards), (r.get("備考") or "").strip()))
    if not rows:
        sys.exit("no sites inside the bounding box")

    # The regions the area is for: where its sites are, unless the manifest
    # says ("area": ["JP-30"]). The app shows and ranks maps by it.
    spec = importlib.util.spec_from_file_location("regions", ROOT / "tool" / "regions.py")
    regions_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regions_mod)
    regions = regions_mod.Regions()
    area = m.get("area")
    if area is None:
        area = sorted({c for c in (regions.code_at(r[4], r[5]) for r in rows) if c})
        if not area:
            sys.exit("no site lies in a known region; set \"area\" in the manifest")
    regions.check(area, manifest_path)

    # Areas without "bundle": false also ship inside the app: copy their
    # sites file to the app's assets/maps/.
    sites_dir = ROOT / "build" / "maps"
    out = sites_dir / f"{m['id']}.sites.sqlite"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sites(uid TEXT, kind TEXT NOT NULL, name TEXT NOT NULL, address TEXT,
                           lat REAL NOT NULL, lon REAL NOT NULL, hazards TEXT NOT NULL, note TEXT);
        CREATE INDEX sites_kind ON sites(kind);
    """)
    meta = {
        "id": m["id"], "title": m["title"], "title_en": m["title_en"],
        "bbox": json.dumps(bbox), "center": json.dumps(m["center"]),
        "sites_licence": sites["licence"], "sites_licence_url": sites["licence_url"],
        "sites_attribution": sites["attribution"], "sites_notice": notice.strip(),
        "basemap_attribution": m["basemap"]["attribution"],
        "area": json.dumps(area),
    }
    db.executemany("INSERT INTO meta VALUES(?,?)", meta.items())
    db.executemany("INSERT INTO sites VALUES(?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.execute("VACUUM")
    db.close()
    em = sum(1 for r in rows if r[1] == "emergency")
    print(f"wrote {out.relative_to(ROOT)}: {em} emergency sites, {len(rows) - em} shelters, "
          f"area {', '.join(area)}")

    tiles = ROOT / "build" / "maps" / f"{m['id']}.pmtiles"
    if not tiles.exists():
        if not shutil.which("pmtiles"):
            sys.exit("pmtiles not found: brew install pmtiles")
        tiles.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["pmtiles", "extract", m["basemap"]["source"], str(tiles),
                        "--bbox=" + ",".join(str(v) for v in bbox), f"--maxzoom={m['maxzoom']}"], check=True)
    print(f"base map: {tiles.relative_to(ROOT)} ({tiles.stat().st_size // (1024 * 1024)} MB)")


def _decodes(raw, enc):
    try:
        raw.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
