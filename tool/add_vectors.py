#!/usr/bin/env python3
"""Adds precomputed passage vectors to a built package, so phones skip indexing.

    python3 -I tool/add_vectors.py build/packages/jp.bousai.core.sqlite VECTORS.json MODEL_NAME

VECTORS.json is the cache the app writes after embedding the package once
(on macOS: ~/Library/Containers/com.lychnion.app/Data/Library/
Application Support/com.lychnion.app/packages/<id>-<version>-<model>.vectors.json).
It lists one vector per passage, in passage id order. MODEL_NAME must be the
embedding model file name the app loads (e.g. embeddinggemma-2-Q8_0.gguf).
"""
import json
import sqlite3
import struct
import sys


def main(package, vectors_json, model):
    vectors = json.load(open(vectors_json))
    db = sqlite3.connect(package)
    ids = [r[0] for r in db.execute("SELECT id FROM passages ORDER BY id")]
    if len(ids) != len(vectors):
        sys.exit(f"{len(vectors)} vectors for {len(ids)} passages: rebuild the cache for this package version")
    dims = {len(v) for v in vectors}
    if len(dims) != 1:
        sys.exit(f"vectors have different lengths: {sorted(dims)}")
    db.execute("CREATE TABLE IF NOT EXISTS vectors(model TEXT NOT NULL, passage_id INTEGER NOT NULL, vec BLOB NOT NULL,"
               " PRIMARY KEY(model, passage_id))")
    db.execute("DELETE FROM vectors WHERE model = ?", (model,))
    db.executemany("INSERT INTO vectors VALUES(?,?,?)",
                   [(model, pid, struct.pack(f"<{len(v)}f", *v)) for pid, v in zip(ids, vectors)])
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"added {len(ids)} vectors ({dims.pop()} dims) for {model}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
