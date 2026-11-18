#!/usr/bin/env python3
"""Checks every "area" in the package and map manifests and in items.json
against the app's region file (see tool/regions.py). Run by CI.

    python3 -I tool/check_areas.py
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("regions", ROOT / "tool" / "regions.py")
regions_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regions_mod)


def main():
    regions = regions_mod.Regions()
    checked = 0
    for f in sorted([*ROOT.glob("packages/*.json"), *ROOT.glob("maps/*.json")]):
        area = json.loads(f.read_text(encoding="utf-8")).get("area")
        if area is not None:
            regions.check(area, f.relative_to(ROOT))
            checked += 1
    for item in json.loads((ROOT / "items.json").read_text(encoding="utf-8"))["items"]:
        if "area" in item:
            regions.check(item["area"], f"items.json {item['id']}")
            checked += 1
    print(f"{checked} areas ok ({len(regions.codes)} regions known)")


if __name__ == "__main__":
    main()
