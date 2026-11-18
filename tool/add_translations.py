#!/usr/bin/env python3
"""Adds passage texts in another language to a built package.

    python3 -I tool/add_translations.py PACKAGE.sqlite LANG --export OUT.json
    python3 -I tool/add_translations.py PACKAGE.sqlite LANG

The phone model must never translate safety advice itself: small models
change the meaning ("snug but not too tight" became 「しっかりと縛って」,
"avalanche" became 「あらわせ」). So each passage gets its text in other
languages once, made by a larger model and reviewed by a person, and the
app reads the passages in the language of the question.

Translations live in packages/translations/<id>.<LANG>.json, the same
format as for foreign sources: {"headings": {...}, "passages": {"<key>":
{"text": ..., "reviewed": false}}}, keyed by a hash of the passage's own
text (tool/build_package.py passage_key). --export writes the passages that
have no translation yet. Without it, every passage must have one; they go
into the table passage_texts (passages and vectors stay as they are).
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def passage_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main(package, lang, export=None):
    db = sqlite3.connect(package)
    pid = db.execute("SELECT value FROM meta WHERE key='id'").fetchone()[0]
    rows = db.execute("SELECT id, heading, text FROM passages ORDER BY id").fetchall()
    tpath = ROOT / "packages" / "translations" / f"{pid}.{lang}.json"
    tr = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else {"headings": {}, "passages": {}}
    missing = [{"key": passage_key(t), "heading": h, "text": t} for _, h, t in rows
               if passage_key(t) not in tr["passages"]]
    missing_h = sorted({h for _, h, _ in rows if h and h not in tr["headings"]})
    if export:
        Path(export).write_text(json.dumps({"headings": missing_h, "passages": missing},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"exported {len(missing)} passages and {len(missing_h)} headings to {export}")
        return
    if missing or missing_h:
        sys.exit(f"{len(missing)} passages and {len(missing_h)} headings have no {lang} translation "
                 f"in {tpath.relative_to(ROOT)}")
    db.execute("CREATE TABLE IF NOT EXISTS passage_texts(passage_id INTEGER NOT NULL, lang TEXT NOT NULL,"
               " heading TEXT, text TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0,"
               " PRIMARY KEY(passage_id, lang))")
    db.execute("DELETE FROM passage_texts WHERE lang = ?", (lang,))
    db.executemany("INSERT INTO passage_texts VALUES(?,?,?,?,?)", [
        (i, lang, tr["headings"].get(h, h) if h else h, tr["passages"][passage_key(t)]["text"],
         1 if tr["passages"][passage_key(t)].get("reviewed") else 0)
        for i, h, t in rows])
    reviewed = sum(1 for e in tr["passages"].values() if e.get("reviewed"))
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
               (f"translation_{lang}", f"Lychnion translation, not official; reviewed {reviewed}/{len(rows)}"))
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"added {len(rows)} {lang} passages to {package}")


if __name__ == "__main__":
    args = sys.argv[1:]
    export = None
    if "--export" in args:
        i = args.index("--export")
        export = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    main(args[0], args[1], export)
