#!/usr/bin/env python3
"""Renders the tsunami inundation areas of one map area as map tiles.

    build/route-venv/bin/python -I tool/build_inundation_tiles.py maps/nishinomiya.json

Uses the same 国土数値情報 A40 files as tool/build_routes.py ("routes.inundation"
in the manifest) and writes build/maps/<id>.inundation.mbtiles (not in git):
transparent 256 px PNG tiles, zoom 10 to 16, coloured by expected depth.
Tiles without any inundation are left out; the app draws nothing there.
The app shows the layer in tsunami mode, with a legend.

MBTiles: SQLite with tables metadata(name, value) and
tiles(zoom_level, tile_column, tile_row, tile_data), rows counted from the
south (TMS), as the MBTiles spec says.
"""
import importlib.util
import io
import json
import math
import sqlite3
import sys
from pathlib import Path

import shapely
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_routes", ROOT / "tool" / "build_routes.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

ZOOMS = range(10, 17)
ALPHA = 140  # of 255: the base map stays readable underneath

# Colour by the lower depth bound (index into build_routes.DEPTHS), light
# yellow for shallow to dark purple for 20 m or more, after the hazard map
# portal's tsunami legend. The app's legend uses the same colours.
COLOURS = {
    0: (255, 245, 160),  # below 0.3 m
    1: (255, 220, 130),  # from 0.3 m
    2: (255, 185, 140),  # from 1 m
    3: (255, 150, 140),  # from 2 m
    4: (240, 115, 150),  # from 3 m
    5: (215, 95, 190),   # from 4 m
    6: (160, 80, 210),   # from 5 m
    7: (110, 60, 190),   # from 10 m
    8: (70, 40, 140),    # from 20 m
}


def tile_bounds(z, x, y):
    """(west, south, east, north) of a tile in degrees."""
    n = 2 ** z

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def to_px(z, x, y, lon, lat):
    n = 2 ** z
    fx = (lon + 180) / 360 * n
    fy = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return (fx - x) * 256, (fy - y) * 256


def rings(g):
    if g.geom_type == "Polygon":
        yield g
    elif hasattr(g, "geoms"):
        for p in g.geoms:
            yield from rings(p)


def main(manifest_path):
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    polys, classes = br.inundation(m["routes"]["inundation"], m["bbox"])
    print(f"{len(polys)} inundation polygons")
    tree = shapely.STRtree(polys)
    out = br.OUT / f"{m['id']}.inundation.mbtiles"
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER,
                            tile_row INTEGER, tile_data BLOB);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    w, s, e, n = m["bbox"]
    meta = {
        "name": f"{m['id']} tsunami inundation",
        "format": "png",
        "type": "overlay",
        "minzoom": str(ZOOMS.start),
        "maxzoom": str(ZOOMS.stop - 1),
        "bounds": f"{w},{s},{e},{n}",
        "attribution": m["routes"].get("inundation_attribution", ""),
    }
    db.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    total = 0
    for z in ZOOMS:
        x0, y0 = (int(v) for v in br._tile_xy(n, w, z))
        x1, y1 = (int(v) for v in br._tile_xy(s, e, z))
        count = 0
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                tw, ts, te, tn = tile_bounds(z, x, y)
                hits = tree.query(shapely.box(tw, ts, te, tn), predicate="intersects")
                if len(hits) == 0:
                    continue
                img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                # Deeper on top, so a deep area is never hidden by a shallow one.
                for j in sorted(hits, key=lambda j: classes[j]):
                    fill = COLOURS[classes[j][0]] + (ALPHA,)
                    for p in rings(polys[j]):
                        pts = [to_px(z, x, y, lon, lat) for lon, lat in p.exterior.coords]
                        if len(pts) < 3:
                            continue
                        draw.polygon(pts, fill=fill)
                        for hole in p.interiors:
                            hp = [to_px(z, x, y, lon, lat) for lon, lat in hole.coords]
                            if len(hp) >= 3:
                                draw.polygon(hp, fill=(0, 0, 0, 0))
                if img.getbbox() is None:
                    continue
                buf = io.BytesIO()
                img.quantize(colors=16, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
                db.execute(
                    "INSERT INTO tiles VALUES (?, ?, ?, ?)",
                    (z, x, (2 ** z) - 1 - y, buf.getvalue()),
                )
                count += 1
        db.commit()
        total += count
        print(f"zoom {z}: {count} tiles")
    db.execute("VACUUM")
    db.close()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {total} tiles)")


if __name__ == "__main__":
    main(sys.argv[1])
