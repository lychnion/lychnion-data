#!/usr/bin/env python3
"""Splits the app's vectors cache of its merged packages into one file per package.

    python3 -I tool/split_vectors.py CACHE.vectors.json OUT_DIR build/packages/<id>.sqlite ...

The app searches its packages merged into one, so its cache covers all of them:
<id1>+<id2>+...-<v1>+<v2>+...-<model>.vectors.json, one vector per passage in that
order. This checks that the ids and versions in the cache's name match the packages
given and that the counts add up (so the vectors belong to these passages), then
writes OUT_DIR/<id>.<model>.vectors.json for each package and prints the
tool/add_vectors.py command for it, with the model taken from the cache's name.
Nothing is written when a check fails.

Run it before changing a package's version: the cache's name carries the versions it
was made from. The same works for the main vectors (<model>) and for the texts in
another language (<model>@en), which only a Mac build with
--dart-define=EMBED_TEXTS=true embeds (lychnion/lychnion#85).
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

SUFFIX = ".vectors.json"


def parse_name(path):
    """(ids, versions, model) from <ids>-<versions>-<model>.vectors.json. Ids and versions
    contain no '-' (checked for the packages given), the model may."""
    name = os.path.basename(path)
    if not name.endswith(SUFFIX):
        sys.exit(f"{name}: not a {SUFFIX} cache")
    parts = name[: -len(SUFFIX)].split("-", 2)
    if len(parts) != 3:
        sys.exit(f"{name}: not <ids>-<versions>-<model>{SUFFIX}")
    ids, versions, model = parts[0].split("+"), parts[1].split("+"), parts[2]
    if len(ids) != len(versions):
        sys.exit(f"{name}: {len(ids)} ids but {len(versions)} versions")
    return ids, versions, model


def package_info(path):
    """(id, version, passage count) of a built package."""
    if not os.path.isfile(path):
        sys.exit(f"{path}: no such package file")
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        meta = dict(db.execute("SELECT key, value FROM meta"))
        n = db.execute("SELECT count(*) FROM passages").fetchone()[0]
    finally:
        db.close()
    if "id" not in meta or "version" not in meta:
        sys.exit(f"{path}: no id or version in its meta")
    if "-" in meta["id"] or "+" in meta["id"]:
        sys.exit(f"{path}: id {meta['id']!r} has '-' or '+', which the cache's name cannot tell apart")
    return meta["id"], meta["version"], n


def split(cache, out_dir, packages):
    ids, versions, model = parse_name(cache)
    given = {}
    for path in packages:
        pid, version, n = package_info(path)
        given[pid] = (version, n, path)
    missing = [i for i in ids if i not in given]
    if missing:
        sys.exit(f"the cache also covers {', '.join(missing)}: give their package files too")
    with open(cache) as f:
        vectors = json.load(f)
    if not isinstance(vectors, list):
        sys.exit(f"{cache}: not a list of vectors")
    parts, start = [], 0
    for pid, version in zip(ids, versions):
        have, n, path = given[pid]
        if have != version:
            sys.exit(f"{pid}: the cache is for version {version}, the package is {have}")
        parts.append((pid, path, vectors[start:start + n]))
        start += n
    if start != len(vectors):
        sys.exit(f"{len(vectors)} vectors for {start} passages: the cache is for other package contents")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for pid, path, part in parts:
        out = os.path.join(out_dir, f"{pid}.{model}{SUFFIX}")
        with open(out, "w") as f:
            json.dump(part, f)
        written.append(out)
        print(f"wrote {len(part)} vectors to {out}; add them with:")
        print(f"  python3 -I tool/add_vectors.py {path} {out} {model}")
    return written


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    split(sys.argv[1], sys.argv[2], sys.argv[3:])
