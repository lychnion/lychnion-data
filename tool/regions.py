"""Region codes for the `area` of packages and map areas.

An area says which places a package or map is about, as ISO 3166 codes:
a country ("JP") or a region ("JP-30", Wakayama). No area means
everywhere (for example first aid). The app uses it to show and rank
downloads for the user's region.

Codes are checked against the region file the app ships with:
assets/regions/regions.sqlite in lychnion/lychnion (built by its
tool/build_regions.py from Natural Earth). It is found by
LYCHNION_REGIONS, else in an app checkout next to this repository
(../lychnion or ../fieldkit).

Used by build_package.py, build_map.py and check_areas.py; import with
importlib (the tools run with python -I).
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")


def regions_file():
    env = os.environ.get("LYCHNION_REGIONS")
    candidates = [Path(env)] if env else [
        ROOT.parent / name / "assets" / "regions" / "regions.sqlite"
        for name in ("lychnion", "fieldkit")
    ]
    for p in candidates:
        if p.is_file():
            return p
    sys.exit("region file not found: set LYCHNION_REGIONS to "
             "assets/regions/regions.sqlite of a lychnion/lychnion checkout")


def _decode(b):
    """Inverse of encode_rings in the app's tool/build_regions.py."""
    pos = 0

    def varint():
        nonlocal pos
        shift = out = 0
        while True:
            byte = b[pos]
            pos += 1
            out |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return out
            shift += 7

    def zigzag(n):
        return (n >> 1) ^ -(n & 1)

    rings = []
    for _ in range(varint()):
        x = y = 0
        ring = []
        for _ in range(varint()):
            x += zigzag(varint())
            y += zigzag(varint())
            ring.append((x / 1e4, y / 1e4))
        rings.append(ring)
    return rings


class Regions:
    def __init__(self, path=None):
        db = sqlite3.connect(f"file:{path or regions_file()}?mode=ro", uri=True)
        self._rows = db.execute(
            "SELECT code, country, min_lon, min_lat, max_lon, max_lat, rings FROM regions"
        ).fetchall()
        db.close()
        self.codes = {r[0] for r in self._rows if not r[0].endswith("~")}
        self.countries = {r[1] for r in self._rows}
        self._rings = {}

    def check(self, area, where):
        """Exits with a message when [area] is not a list of known codes."""
        if not isinstance(area, list) or not area:
            sys.exit(f"{where}: area must be a non-empty list of codes")
        for code in area:
            if not isinstance(code, str) or not CODE.match(code):
                sys.exit(f"{where}: {code!r} is not a code like JP or JP-30")
            if code not in self.codes and code not in self.countries:
                sys.exit(f"{where}: unknown region {code}")
        if len(set(area)) != len(area):
            sys.exit(f"{where}: area lists a code twice")

    def code_at(self, lat, lon):
        """The region containing the point (even-odd rule), else None."""
        for code, _, x0, y0, x1, y1, blob in self._rows:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            rings = self._rings.get(code)
            if rings is None:
                rings = self._rings[code] = _decode(blob)
            inside = False
            for ring in rings:
                j = len(ring) - 1
                for i in range(len(ring)):
                    xi, yi = ring[i]
                    xj, yj = ring[j]
                    if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                        inside = not inside
                    j = i
            if inside:
                return code
        return None

