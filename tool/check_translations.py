#!/usr/bin/env python3
"""Checks stored translations for changes in meaning, and writes a report
for the person who reviews them.

    python3 -I tool/check_translations.py [packages/translations/<id>.<lang>.json ...]

Without files it checks every translation file. The build runs it too
(tool/build_package.py, tool/add_translations.py), and so does CI.

Each passage entry holds its original ("source"), the translation ("text")
and "reviewed". The checks:

- **numbers** (error): the same numbers in original and translation, after
  reading full-width digits, Japanese era years (令和8年 = Reiwa 8 = 2026),
  month names and small English number words; and the same unit where both
  sides give one (minutes, °C, cm, litres, ...). Fails the build.
- **words** (warning): degree, negation and urgency words that appear on
  one side only, from the reviewed word list
  packages/translations/terms.<a>-<b>.json ("not too tight" without
  「締めすぎない」, 「縛って」 without "tie"), or that are in more sentences
  on one side ("do not" twice, 「しない」 once).
- **integrity** (error): the key is the hash of the original, and the
  translation is not empty.

A flag that a person has checked and found fine gets an exception in the
entry, with a reason: "accept": [{"check": "numbers", "item": "2026",
"reason": "..."}]. "item" is what the flag names (a number, or a word group
id). Exceptions that no longer match a flag are listed, so they do not pile
up. The tool never changes "reviewed".

The report per file goes to build/translation-checks/<id>.<lang>.md: every
flagged passage with its original and translation. Exit status 1 when an
error is left.
"""
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS = ROOT / "packages" / "translations"
REPORTS = ROOT / "build" / "translation-checks"


def passage_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---- numbers ---------------------------------------------------------------

ERAS = {"令和": 2018, "平成": 1988, "昭和": 1925, "reiwa": 2018, "heisei": 1988, "showa": 1925}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
         "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100}
KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# What follows a number, by language: canonical unit names.
UNITS = {
    "en": [(r"°\s?c\b|degrees? (?:celsius|c\b)|℃", "°C"), (r"°\s?f\b|degrees? (?:fahrenheit|f\b)", "°F"),
           (r"min(?:ute)?s?\b", "min"), (r"h(?:ou)?rs?\b|hours?\b", "h"), (r"sec(?:ond)?s?\b", "s"),
           (r"days?\b", "day"), (r"weeks?\b", "week"), (r"months?\b", "month"), (r"years?\b", "year"),
           (r"cm\b|centimet(?:er|re)s?\b", "cm"), (r"mm\b|millimet(?:er|re)s?\b", "mm"),
           (r"km\b|kilomet(?:er|re)s?\b", "km"), (r"m\b|met(?:er|re)s?\b", "m"),
           (r"kg\b|kilograms?\b", "kg"), (r"mg\b|milligrams?\b", "mg"), (r"g\b|grams?\b", "g"),
           (r"ml\b|millilit(?:er|re)s?\b", "ml"), (r"l\b|lit(?:er|re)s?\b", "l"),
           (r"%|percent\b|per cent\b", "%"), (r"people\b|persons?\b", "person"),
           (r"times\b", "times")],
    "ja": [(r"℃|°C|度", "°C"), (r"°F", "°F"), (r"分(?!の)", "min"), (r"時間", "h"), (r"秒", "s"),
           (r"日", "day"), (r"週間?", "week"), (r"[かカヶケ]月", "month"), (r"年", "year"),
           (r"cm|センチ", "cm"), (r"mm|ミリ(?!リットル|グラム)", "mm"), (r"km|キロ(?!グラム)", "km"),
           (r"m(?![lgL])|メートル", "m"), (r"kg|キログラム", "kg"), (r"mg|ミリグラム", "mg"),
           (r"g|グラム", "g"), (r"ml|mL|ミリリットル", "ml"), (r"[lL]|リットル", "l"),
           (r"%", "%"), (r"人", "person"), (r"回", "times")],
}
JA_COUNTERS = "つ|回|分|時間|日|人|度|週間|か月|ヶ月|カ月|年|歳|倍|枚|本|個|件|段階|か所|箇所"


def _canon(n):
    return str(int(float(n))) if float(n).is_integer() else str(float(n))


def _normalize(text, lang):
    t = unicodedata.normalize("NFKC", text.replace("º", "°"))  # NFKC makes º an "o"
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)  # 3,122
    # Figure references, which translations may leave out.
    t = re.sub(r"\((?:see )?(?:figure|table) [\d-]+(?: on page \d+)?\.?\)|[(]図\s?[\d-]+[)]", " ", t, flags=re.I)
    if lang == "en":
        # Paragraph numbers, chapter lines, page footers and figure titles
        # of the US manuals ("16-40. In the event of ...", "50 TC 21-3").
        t = re.sub(r"(?m)^\s*\d+-\d+\.\s", " ", t)
        t = re.sub(r"(?m)^(?:Chapter \d+|\d+ (?:TC|ATP) [\d.-]+|(?:TC|ATP) [\d.-]+ \d+|Figure \d+-\d+\..*)$",
                   " ", t)
        t = re.sub(r"\btwice\b", "2 times", t, flags=re.I)
        t = re.sub(r"\bonce(?= (?:a|per) (?:day|week|month|year)\b| daily\b)", "1 times", t, flags=re.I)
        t = re.sub(r"\bper (day|person)\b|(?<=times) (?:daily|a day)\b|(?<=2 times) daily\b",
                   lambda m: f" 1 {m[1] or 'day'} ", t, flags=re.I)
        t = re.sub(r"\bevery hour\b|\bhourly\b", " 1 hour ", t, flags=re.I)
        # Reiwa 8 (2026): one year, not two numbers.
        t = re.sub(r"\b(Reiwa|Heisei|Showa) (\d+)(?: \((\d{4})\))?",
                   lambda m: f" {ERAS[m[1].lower()] + int(m[2])} ", t, flags=re.I)
        # January 1 = 1月1日
        names = "|".join(m.capitalize() for m in MONTHS if m != "may") + r"|May(?= \d)"
        t = re.sub(r"\b(" + names + r")(?: (\d{1,2})(?:st|nd|rd|th)?\b(?!,? \d{4}\b(?! ))| (?=\d{4}))?",
                   lambda m: f" {MONTHS.index(m[1].lower()) + 1} month " + (f"{m[2]} day " if m[2] else ""), t)
        t = re.sub(r"(?<!than )\b(?:one|a single)(?= (?:hour|minute|second|day|week|month|year|person|time)s?\b)",
                   "1", t, flags=re.I)
        t = re.sub(r"\bthe first one\b", "1", t, flags=re.I)
        t = re.sub(r"(?<!every )\b(second|third|fourth|fifth)\b(?! of)",
                   lambda m: str(["second", "third", "fourth", "fifth"].index(m[1].lower()) + 2), t)
        t = re.sub(r"\b(" + "|".join(WORDS) + r")\b", lambda m: str(WORDS[m[1].lower()]), t, flags=re.I)
    else:
        t = re.sub(r"(令和|平成|昭和)(元|\d+)年",
                   lambda m: f"{ERAS[m[1]] + (1 if m[2] == '元' else int(m[2]))}年", t)
        # 令和6年（2024年）: one year
        t = re.sub(r"(\d{4})年(度|中)?\(\1年?(?:度|中)?\)", r"\1年\2", t)
        # 二つ, 三日, 四輪 (not 十分, "enough")
        t = re.sub(r"(?<![一-鿿])([二三四五六七八九十])(?!分)(?=" + JA_COUNTERS + "|輪)",
                   lambda m: str(KANJI[m[1]]), t)
        t = re.sub(r"三が日", "3日間", t)
        # 10万, 19万9千, 8割 (= 80 %)
        t = re.sub(r"(\d+(?:\.\d+)?)万(?:(\d+)千)?",
                   lambda m: _canon(float(m[1]) * 10000 + int(m[2] or 0) * 1000), t)
        t = re.sub(r"(\d+)千(?!葉)", lambda m: str(int(m[1]) * 1000), t)
        t = re.sub(r"(\d+)割", lambda m: f"{int(m[1]) * 10}%", t)
    return t


def numbers(text, lang):
    """Counter of numbers, and for each number the units found after it."""
    t = _normalize(text, lang)
    found, units = Counter(), {}
    for m in re.finditer(r"(?<![\d.])\d+(?:\.\d+)?", t):
        n = _canon(m[0])
        found[n] += 1
        rest = t[m.end():m.end() + 24]
        rest = rest.lstrip() if lang == "en" else rest
        for pattern, unit in UNITS["ja" if lang == "ja" else "en"]:
            if re.match(pattern, rest, flags=re.I if lang == "en" else 0):
                units.setdefault(n, Counter())[unit] += 1
                break
    return found, units


def check_numbers(src, src_lang, dst, dst_lang):
    a, au = numbers(src, src_lang)
    b, bu = numbers(dst, dst_lang)
    flags = []
    for n in sorted(set(a) | set(b), key=lambda x: float(x)):
        if a[n] > b[n]:
            flags.append((n, f"{n} in the original{_times(a[n] - b[n])}, missing in the translation"))
        elif b[n] > a[n]:
            flags.append((n, f"{n} in the translation{_times(b[n] - a[n])}, not in the original"))
        elif n in au and n in bu and not set(au[n]) & set(bu[n]):
            flags.append((n, f"{n} has unit {'/'.join(sorted(au[n]))} in the original, "
                             f"{'/'.join(sorted(bu[n]))} in the translation"))
    return flags


def _times(k):
    return "" if k == 1 else f" ({k} more times)"


# ---- words -----------------------------------------------------------------

def load_terms(a, b):
    """The word list for a language pair, as {group: {lang: [regex]}}."""
    for name in (f"terms.{a}-{b}.json", f"terms.{b}-{a}.json"):
        path = TRANSLATIONS / name
        if path.exists():
            groups = json.loads(path.read_text(encoding="utf-8"))["groups"]
            return {g["id"]: {lang: [re.compile(p, re.I | re.M) for p in g[lang]] for lang in (a, b)}
                    for g in groups}
    return {}


def sentences(text, lang):
    """Splits into sentences and list items (hard line wraps joined)."""
    t = unicodedata.normalize("NFKC", text)
    if lang == "en":
        t = re.sub(r"\n(?![•\-*]|\d+-\d+\.)", " ", t)
        parts = re.split(r"(?<=[.!?:])\s+(?=[A-Z•(\"])|\n|•", t)
    else:
        parts = re.split(r"(?<=[。！？])|\n|・", t)
    return [p.strip() for p in parts if p and len(p.strip()) > 1]


def _has(patterns, text):
    return any(p.search(text) for p in patterns)


def check_words(src, src_lang, dst, dst_lang, terms):
    """Word groups found in a different number of sentences on each side.
    Counting sentences (not pairing them by position) keeps a sentence the
    translator split in two from looking like a lost word."""
    if not terms:
        return []
    sa, sb = sentences(src, src_lang), sentences(dst, dst_lang)
    flags = []
    for gid, pats in terms.items():
        na = sum(1 for x in sa if _has(pats[src_lang], x))
        nb = sum(1 for y in sb if _has(pats[dst_lang], y))
        if na != nb:
            if not nb or not na:
                flags.append((gid, f"{gid}: in the {'original' if na else 'translation'} only"))
            else:
                flags.append((gid, f"{gid}: in {na} sentences of the original, {nb} of the translation"))
    return sorted(flags)


# ---- one file --------------------------------------------------------------

def langs(path):
    """(source language, target language) of packages/translations/<id>.<lang>.json."""
    pid, dst = Path(path).name[:-len(".json")].rsplit(".", 1)
    manifest = ROOT / "packages" / f"{pid}.json"
    src = json.loads(manifest.read_text(encoding="utf-8")).get("language", "ja") if manifest.exists() else "ja"
    return pid, src, dst


def check_entry(key, e, src_lang, dst_lang, terms):
    """Flags of one passage: [(check, item, message, level)], and the exceptions not used."""
    flags = []
    src, text = e.get("source"), e.get("text", "")
    if not src:
        return [("integrity", "source", "no original (\"source\") stored next to the translation", "error")], []
    if passage_key(src) != key:
        flags.append(("integrity", "key", f"key {key} is not the hash of the stored original "
                                          f"({passage_key(src)})", "error"))
    if not text.strip():
        flags.append(("integrity", "text", "translation is empty", "error"))
    flags += [("numbers", n, msg, "error") for n, msg in check_numbers(src, src_lang, text, dst_lang)]
    flags += [("words", g, msg, "warning") for g, msg in check_words(src, src_lang, text, dst_lang, terms)]
    accepted = {(a.get("check"), str(a.get("item"))): a.get("reason", "") for a in e.get("accept", [])}
    out = []
    for check, item, msg, level in flags:
        reason = accepted.get((check, item))
        if reason is not None and check != "integrity" and reason.strip():
            out.append((check, item, f"{msg} — accepted: {reason}", "accepted"))
        else:
            out.append((check, item, msg, level))
    used = {(c, i) for c, i, _, lvl in out if lvl == "accepted"}
    unused = [f"{c} {i}" for c, i in accepted if (c, i) not in used]
    return out, unused


def check_file(path, report_dir=REPORTS, quiet=False):
    """Checks one translation file and writes its report. Returns (errors, warnings)."""
    path = Path(path)
    pid, src_lang, dst_lang = langs(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    terms = load_terms(src_lang, dst_lang)
    results = []
    for key, e in data["passages"].items():
        flags, unused = check_entry(key, e, src_lang, dst_lang, terms)
        if flags or unused:
            results.append((key, e, flags, unused))
    heading_flags = []
    for orig, tr in data.get("headings", {}).items():
        heading_flags += [(orig, tr, msg) for _, msg in check_numbers(orig, src_lang, tr, dst_lang)]
    errors = sum(1 for _, _, f, _ in results for x in f if x[3] == "error") + len(heading_flags)
    warnings = sum(1 for _, _, f, _ in results for x in f if x[3] == "warning")
    reviewed = sum(1 for e in data["passages"].values() if e.get("reviewed"))
    report = _report(path, pid, src_lang, dst_lang, data, results, heading_flags, errors, warnings, reviewed)
    report_dir.mkdir(parents=True, exist_ok=True)
    out = report_dir / f"{pid}.{dst_lang}.md"
    out.write_text(report, encoding="utf-8")
    if not quiet:
        flagged = sum(1 for r in results if any(x[3] in ("error", "warning") for x in r[2]))
        print(f"{path.name}: {len(data['passages'])} passages, {reviewed} reviewed; "
              f"{flagged} flagged: {errors} errors, {warnings} warnings → {_rel(out)}")
        for key, e, flags, _ in results:
            for check, item, msg, level in flags:
                if level == "error":
                    print(f"  error {key} {check}: {msg}")
        for orig, _, msg in heading_flags:
            print(f"  error heading {orig!r} numbers: {msg}")
    return errors, warnings


def _rel(p):
    try:
        return Path(p).resolve().relative_to(ROOT)
    except ValueError:
        return p


def _quote(text):
    return "\n".join("> " + line for line in text.strip().splitlines())


def _report(path, pid, a, b, data, results, heading_flags, errors, warnings, reviewed):
    n = len(data["passages"])
    lines = [f"# Translation check: {pid} ({a} → {b})", "",
             f"File: `{_rel(path)}`. {n} passages, {reviewed} reviewed by a person. "
             f"Flagged: {errors} errors (numbers, integrity: fail the build), "
             f"{warnings} warnings (degree and negation words: for the reviewer).", "",
             "Machine checks only point at places to look; a passage without a flag is not checked "
             "by them. Every passage still needs the review by a person.", ""]
    if heading_flags:
        lines += ["## Headings", ""]
        lines += [f"- **error** {msg}: `{o}` → `{t}`" for o, t, msg in heading_flags]
        lines.append("")
    order = {"error": 0, "warning": 1, "accepted": 2}
    results = sorted(results, key=lambda r: min((order[x[3]] for x in r[2]), default=3))
    for key, e, flags, unused in results:
        state = "reviewed" if e.get("reviewed") else "not reviewed"
        lines += [f"## {key} ({state})", ""]
        lines += [f"- **{level}** {check}: {msg}" for check, _, msg, level in flags]
        lines += [f"- **unused exception** {u}: no such flag any more; remove it" for u in unused]
        lines += ["", "Original:", "", _quote(e.get("source") or ""), "",
                  "Translation:", "", _quote(e.get("text", "")), ""]
    if not results and not heading_flags:
        lines.append("No flags.")
    return "\n".join(lines) + "\n"


# ---- updates ---------------------------------------------------------------

def changes(tr, rows):
    """For the build: passages without a translation (new or changed since the
    translation was made) and stored translations no source passage uses any
    more (removed or changed). rows: [(heading, text)]."""
    keys = {passage_key(t) for _, t in rows}
    new = [(h, t) for h, t in rows if passage_key(t) not in tr["passages"]]
    stale = [(k, e) for k, e in tr["passages"].items() if k not in keys]
    return new, stale


def print_changes(tr, rows, where):
    new, stale = changes(tr, rows)
    for h, t in new:
        print(f"  new or changed: {passage_key(t)} [{h}] {_first(t)}")
    for k, e in stale:
        print(f"  no longer in the source: {k} {_first(e.get('source') or e.get('text', ''))}")
    if new or stale:
        print(f"{where}: {len(new)} passages need a translation, "
              f"{len(stale)} stored translations are no longer used")
    return new, stale


def _first(text, n=70):
    line = " ".join(text.split())
    return line if len(line) <= n else line[:n] + "…"


def main(files):
    files = files or sorted(p for p in TRANSLATIONS.glob("*.json") if not p.name.startswith("terms."))
    errors = 0
    for f in files:
        errors += check_file(f)[0]
    if errors:
        sys.exit(f"{errors} translation errors: fix the translation, or add an exception with a reason")


if __name__ == "__main__":
    main(sys.argv[1:])
