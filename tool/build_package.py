#!/usr/bin/env python3
"""Builds a knowledge package (one SQLite file) from a JSON manifest.

    python3 -I tool/build_package.py packages/jp.bousai.core.json [--max-chars N]
        [--export-untranslated FILE] [--meta-only]

Downloads each source once into build/package-cache/ (or reads a saved text
snapshot, for sites that refuse scripted downloads), extracts the text
(HTML, Word .doc via macOS textutil, PDF via tool/pdf2txt.swift), splits it
into short passages and writes build/packages/<id>.sqlite.

Per source, optional: skip_headings, skip_heading_prefixes,
drop_lines_containing, end_marker (drop the menu or footer after the
content), min_japanese_ratio (drop tables of numbers). Per manifest:
skip_headings_all, drop_lines_containing, drop_lines (exact lines),
drop_lines_matching (regular expressions for whole lines, e.g. page
numbers), region (ISO 3166 for all sources).

Every fixed safety line ("alert") must appear word for word in its source,
otherwise the build fails.
"""
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import urllib.request
import importlib.util
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "package-cache"
OUT = ROOT / "build" / "packages"
MAX_CHARS = 420

# Page furniture on kantei.go.jp and similar sites, not content.
DROP_LINES = {"子項目を開閉", "この記事で伝えたいトピックス", "関連リンク", "ページの先頭へ", "印刷"}


class _Text(HTMLParser):
    BLOCK = {"p", "li", "div", "tr", "br", "td", "th", "dd", "dt"}
    HEAD = {"h1", "h2", "h3", "h4"}
    SKIP = {"script", "style", "nav", "header", "footer", "noscript"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.HEAD:
            self.out.append("\n## ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.HEAD:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def fetch(src, manifest_dir):
    if src.get("snapshot"):
        # Text saved from a browser for sites that refuse scripted downloads.
        return manifest_dir / src["snapshot"]
    ext = Path(src["url"].split("?")[0]).suffix
    if ext not in (".pdf", ".doc", ".txt"):
        ext = ".html"  # .php, .aspx and pages without an extension
    path = CACHE / f"{src['id']}{ext}"
    if not path.exists():
        req = urllib.request.Request(src["url"], headers={"User-Agent": "lychnion-package-builder"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_bytes(r.read())
    return path


def extract(path):
    if path.suffix in (".html", ".htm"):
        p = _Text()
        p.feed(path.read_bytes().decode("utf-8", "replace"))
        return "".join(p.out)
    if path.suffix == ".txt":
        return path.read_text(encoding="utf-8")
    if path.suffix == ".doc":
        return subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                              check=True, capture_output=True, text=True).stdout
    if path.suffix == ".pdf":
        text = subprocess.run(["swift", str(ROOT / "tool" / "pdf2txt.swift"), str(path)],
                              check=True, capture_output=True, text=True).stdout
        if _japanese_ratio(text) < 0.3:
            # Broken font encoding: read the rendered pages instead.
            print(f"{path.name}: text layer unreadable, using OCR")
            text = subprocess.run(["swift", str(ROOT / "tool" / "pdf_ocr.swift"), str(path)],
                                  check=True, capture_output=True, text=True).stdout
        return text
    raise ValueError(f"unsupported file type: {path}")


def _japanese_ratio(text):
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    jp = sum(1 for c in chars if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    return jp / len(chars)


def clean_lines(text):
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t　]+", " ", raw).strip()
        if not line or line in DROP_LINES or line == "##":
            continue
        lines.append(line)
    return lines


def passages(lines, max_chars=MAX_CHARS):
    """Groups lines into passages of up to MAX_CHARS, each with its heading."""
    heading, buf, out = "", [], []

    def flush():
        if buf:
            out.append((heading, "\n".join(buf)))
            buf.clear()

    for line in lines:
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            continue
        if buf and sum(len(b) for b in buf) + len(line) > max_chars:
            flush()
        buf.append(line)
    flush()
    # Very short passages (menus, captions) carry no usable guidance.
    return [(h, t) for h, t in out if len(t) >= 25]


def passage_key(text):
    """Stable key of an original passage, for its stored translation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


EXPORT = None


def squash(s):
    return re.sub(r"\s+", "", s)


def area_meta(manifest, where):
    """The manifest's optional "area" (places the package is for, e.g.
    ["JP"] or ["JP-30"]; none: anywhere), checked against the app's region
    file, as stored in meta (JSON, or "" for none)."""
    area = manifest.get("area")
    if area is None:
        return ""
    spec = importlib.util.spec_from_file_location("regions", ROOT / "tool" / "regions.py")
    regions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regions)
    regions.Regions().check(area, where)
    return json.dumps(area)


def main(manifest_path, max_chars=MAX_CHARS):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    area = area_meta(manifest, manifest_path)
    if max_chars != MAX_CHARS:
        # A different cut is a different package: vectors cached per version.
        manifest["version"] += f"+c{max_chars}"
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    texts, rows = {}, []
    manifest["sources"] = [s for s in manifest["sources"] if not s.get("disabled")]
    for src in manifest["sources"]:
        text = extract(fetch(src, Path(manifest_path).parent))
        texts[src["id"]] = squash(text)
        # e.g. cover, contents and link-list sections, and video captions
        skip = set(src.get("skip_headings", []))
        if not src.get("own_skip_headings_only"):
            skip |= set(manifest.get("skip_headings_all", []))
        drop = manifest.get("drop_lines_containing", []) + src.get("drop_lines_containing", [])
        exact = set(manifest.get("drop_lines", []))
        patterns = [re.compile(x) for x in manifest.get("drop_lines_matching", [])]
        lines = clean_lines(text)
        if src.get("end_marker"):
            # Page footer or site menu after the content.
            end = next((i for i, l in enumerate(lines) if src["end_marker"] in l), None)
            if end is None:
                sys.exit(f"{src['id']}: end_marker not found: {src['end_marker']}")
            lines = lines[:end]
        lines = [l for l in lines if l not in exact and not any(d in l for d in drop)
                 and not any(p.fullmatch(l) for p in patterns)]
        prefixes = tuple(src.get("skip_heading_prefixes", []))
        # Tables of numbers (statistics) carry no guidance: optional cut-off
        # for the share of Japanese letters in a passage.
        min_ja = src.get("min_japanese_ratio", manifest.get("min_japanese_ratio", 0))
        ps = [(h, t) for h, t in passages(lines, max_chars)
              if h not in skip and not (prefixes and h.startswith(prefixes))
              and _japanese_ratio(t) >= min_ja]
        if not ps:
            sys.exit(f"no text extracted from {src['id']}")
        rows += [(src["id"], h, t) for h, t in ps]
        print(f"{src['id']}: {len(ps)} passages")

    # Foreign sources: the phone model must never translate safety advice
    # itself (it turned "snug but not too tight" into 「しっかりと縛って」).
    # Each passage gets a translation made once, stored in the repository
    # (translations/<id>.<lang>.json, checked by a person), and the original
    # is kept next to it.
    originals = {}
    lang = manifest.get("translate_to")
    if lang:
        tpath = Path(manifest_path).parent / "translations" / f"{manifest['id']}.{lang}.json"
        tr = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else {"headings": {}, "passages": {}}
        missing = [{"key": passage_key(t), "heading": h, "text": t} for _, h, t in rows
                   if passage_key(t) not in tr["passages"]]
        missing_h = sorted({h for _, h, _ in rows if h and h not in tr["headings"]})
        if EXPORT:
            Path(EXPORT).write_text(json.dumps({"headings": missing_h, "passages": missing},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
            sys.exit(f"exported {len(missing)} passages and {len(missing_h)} headings to {EXPORT}")
        if missing or missing_h:
            sys.exit(f"{len(missing)} passages and {len(missing_h)} headings have no {lang} translation "
                     f"in {tpath.relative_to(ROOT)}; export them with --export-untranslated FILE")
        translated = []
        for sid, h, t in rows:
            e = tr["passages"][passage_key(t)]
            originals[len(translated)] = (h, t)
            translated.append((sid, tr["headings"].get(h, h) if h else h, e["text"]))
        rows = translated
        reviewed = sum(1 for e in tr["passages"].values() if e.get("reviewed"))
        manifest["translation"] = f"{lang}: Lychnion translation, not official; reviewed {reviewed}/{len(rows)}"

    for alert in manifest.get("alerts", []):
        # Each sentence of a fixed safety line must be in the source as written.
        for sentence in filter(None, alert["text"].split("。")):
            if squash(sentence) not in texts[alert["source"]]:
                sys.exit(f"alert {alert['id']}: not found in {alert['source']}: {sentence}")

    out = OUT / f"{manifest['id']}.sqlite"
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sources(id TEXT PRIMARY KEY, title TEXT, publisher TEXT, url TEXT,
                             licence TEXT, licence_url TEXT, attribution TEXT,
                             region TEXT NOT NULL DEFAULT 'JP');
        CREATE TABLE passages(id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                              heading TEXT, text TEXT NOT NULL,
                              original_heading TEXT, original TEXT);
        CREATE VIRTUAL TABLE passages_fts USING fts5(heading, text, content='passages',
                              content_rowid='id', tokenize='trigram');
        CREATE TABLE alerts(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                            text TEXT NOT NULL, match_all TEXT NOT NULL,
                            text_en TEXT, match_all_en TEXT);
    """)
    for k in ("id", "title", "language", "version", "note", "translation"):
        db.execute("INSERT INTO meta VALUES(?,?)", (k, manifest.get(k, "")))
    # What the package is not for, shown to users before they rely on it.
    db.execute("INSERT INTO meta VALUES('notice',?)", (json.dumps(manifest.get("notice", {}), ensure_ascii=False),))
    # Shown name per language, an icon name and suggested first questions
    # per language: the app shows them without knowing the package.
    db.execute("INSERT INTO meta VALUES('name',?)", (json.dumps(manifest.get("name", {}), ensure_ascii=False),))
    db.execute("INSERT INTO meta VALUES('icon',?)", (manifest.get("icon", ""),))
    db.execute("INSERT INTO meta VALUES('suggestions',?)", (json.dumps(manifest.get("suggestions", {}), ensure_ascii=False),))
    # Places the package is for (see tool/regions.py); "" for anywhere.
    db.execute("INSERT INTO meta VALUES('area',?)", (area,))
    db.execute("INSERT INTO meta VALUES('gaps',?)", (json.dumps(manifest.get("gaps", []), ensure_ascii=False),))
    # Query expansion for keyword search, until vector search is added.
    db.execute("INSERT INTO meta VALUES('synonyms',?)", (json.dumps(manifest.get("synonyms", {}), ensure_ascii=False),))
    # Where the advice applies (ISO 3166: JP, JP-13, GB, US); the app ranks
    # local sources a little higher and labels foreign ones.
    region = manifest.get("region", "JP")
    db.executemany("INSERT INTO sources VALUES(:id,:title,:publisher,:url,:licence,:licence_url,:attribution,:region)",
                   [{**{k: s.get(k) for k in ("id", "title", "publisher", "url", "licence", "licence_url", "attribution")},
                     "region": s.get("region", region)}
                    for s in manifest["sources"]])
    db.executemany("INSERT INTO passages(source_id,heading,text,original_heading,original) VALUES(?,?,?,?,?)",
                   [(sid, h, t, *originals.get(i, (None, None))) for i, (sid, h, t) in enumerate(rows)])
    db.execute("INSERT INTO passages_fts(passages_fts) VALUES('rebuild')")
    # text_en is our translation, shown next to the official Japanese text.
    db.executemany("INSERT INTO alerts VALUES(?,?,?,?,?,?)",
                   [(a["id"], a["source"], a["text"], json.dumps(a["all"], ensure_ascii=False),
                     a.get("text_en"), json.dumps(a.get("all_en", []), ensure_ascii=False))
                    for a in manifest.get("alerts", [])])
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} passages, {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    args = sys.argv[1:]
    chars = MAX_CHARS
    if "--max-chars" in args:
        i = args.index("--max-chars")
        chars = int(args[i + 1])
        del args[i:i + 2]
    if "--meta-only" in args:
        # Update the notice, title, note and area of a built package without
        # rebuilding it (keeps its passages and vectors).
        args.remove("--meta-only")
        m = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        db = sqlite3.connect(OUT / f"{m['id']}.sqlite")
        for k, v in (("notice", json.dumps(m.get("notice", {}), ensure_ascii=False)),
                     ("name", json.dumps(m.get("name", {}), ensure_ascii=False)),
                     ("icon", m.get("icon", "")),
                     ("suggestions", json.dumps(m.get("suggestions", {}), ensure_ascii=False)),
                     ("title", m.get("title", "")), ("note", m.get("note", "")),
                     ("area", area_meta(m, args[0]))):
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
        db.commit()
        db.close()
        sys.exit(0)
    if "--export-untranslated" in args:
        i = args.index("--export-untranslated")
        EXPORT = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit(__doc__)
    main(args[0], chars)
