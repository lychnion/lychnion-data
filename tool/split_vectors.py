#!/usr/bin/env python3
"""Splits the app's vectors cache of its merged packages into one file per package.

    python3 -I tool/split_vectors.py CACHE.vectors.json OUT_DIR build/packages/<id>.sqlite ...

The app searches its packages merged into one, so its cache covers all of them:
<id1>+<id2>+...-<v1>+<v2>+...-<model>.vectors.json, one vector per passage in that
order. This writes OUT_DIR/<id>.vectors.json for each package given, after checking
that the ids and versions in the cache's name match the packages (so the vectors
belong to these passages), for tool/add_vectors.py.

The texts in another language (English) are embedded only by a Mac build with
--dart-define=EMBED_TEXTS=true; their cache ends in <model>@en.vectors.json and is
added with the model name <model>@en (lychnion/lychnion#85).
"""
import json
import os
import sqlite3
import sys


def parse_name(path):
    """(ids, versions, model) from <ids>-<versions>-<model>.vectors.json."""
    name = os.path.basename(path)
    if not name.endswith(".vectors.json"):
        sys.exit(f"{name}: not a .vectors.json cache")
    ids, versions, model = name[: -len(".vectors.json")].split("-", 2)
    ids, versions = ids.split("+"), versions.split("+")
    if len(ids) != len(versions):
        sys.exit(f"{name}: {len(ids)} ids but {len(versions)} versions")
    return ids, versions, model


def split(cache, out_dir, packages):
    ids, versions, model = parse_name(cache)
    vectors = json.load(open(cache))
    given = {}
    for path in packages:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        meta = dict(db.execute("SELECT key, value FROM meta"))
        given[meta["id"]] = (meta["version"], db.execute("SELECT count(*) FROM passages").fetchone()[0])
        db.close()
    missing = [i for i in ids if i not in given]
    if missing:
        sys.exit(f"the cache also covers {', '.join(missing)}: give their package files too")
    start = 0
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for pid, version in zip(ids, versions):
        have, n = given[pid]
        if have != version:
            sys.exit(f"{pid}: the cache is for version {version}, the package is {have}")
        out = os.path.join(out_dir, f"{pid}.vectors.json")
        with open(out, "w") as f:
            json.dump(vectors[start:start + n], f)
        written.append((out, n))
        start += n
    if start != len(vectors):
        sys.exit(f"{len(vectors)} vectors for {start} passages: the cache is for other package contents")
    for out, n in written:
        print(f"wrote {n} vectors ({model}) to {out}")
    return written


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    split(sys.argv[1], sys.argv[2], sys.argv[3:])
