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
"for": "<hash of the translation text>", "reason": "..."}]. "item" is what
the flag names (a number, or a word group id); "for" ties the exception to
the translation as it was checked (the error message gives it), so an edit
brings the flag back. Headings take the same list in "accept_headings":
{"<original heading>": [...]}. Exceptions that no longer match a flag or a
text are listed, so they do not pile up. The tool never changes "reviewed".

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
         "thirteen": 13, "fourteen": 14, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90, "hundred": 100}
TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
ONES = "one|two|three|four|five|six|seven|eight|nine"
KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# What follows a number, by language: canonical unit names.
UNITS = {
    "en": [(r"°\s?c\b|degrees? (?:celsius|c\b)|℃", "°C"), (r"°\s?f\b|degrees? (?:fahrenheit|f\b)", "°F"),
           (r"°|degrees?\b", "deg"), (r"mcg\b|µg\b|μg\b|micrograms?\b", "mcg"), (r"mmhg\b", "mmHg"),
           (r"iu\b|international units?\b|units?\b", "IU"), (r"cc\b", "ml"), (r"(?:tablet|pill)s?\b", "tablet"),
           (r"drops?\b", "drop"), (r"gm\b", "g"), (r"tsp\b|teaspoons?\b", "tsp"),
           (r"tbsp\b|tablespoons?\b", "tbsp"), (r"cups?\b", "cup"),
           (r"min(?:ute)?s?\b", "min"), (r"h(?:ou)?rs?\b|hours?\b|h\b", "h"), (r"sec(?:ond)?s?\b", "s"),
           (r"days?\b", "day"), (r"weeks?\b", "week"), (r"months?\b", "month"), (r"years?\b", "year"),
           (r"cm\b|centimet(?:er|re)s?\b", "cm"), (r"mm\b|millimet(?:er|re)s?\b", "mm"),
           (r"km\b|kilomet(?:er|re)s?\b", "km"), (r"m\b|met(?:er|re)s?\b", "m"),
           (r"kg\b|kilograms?\b", "kg"), (r"mg\b|milligrams?\b", "mg"), (r"g\b|grams?\b", "g"),
           (r"ml\b|millilit(?:er|re)s?\b", "ml"), (r"l\b|lit(?:er|re)s?\b", "l"),
           (r"%|percent\b|per cent\b", "%"), (r"people\b|persons?\b", "person"),
           (r"times\b", "times")],
    # 度 alone is Celsius in Japanese (or an angle, which English writes as degrees)
    "ja": [(r"℃|°C", "°C"), (r"°F", "°F"), (r"°|度(?!と)", "°C"), (r"tsp", "tsp"), (r"tbsp", "tbsp"),
           (r"カップ|cup", "cup"), (r"mcg|µg|μg|マイクログラム", "mcg"),
           (r"mmHg", "mmHg"), (r"IU|単位", "IU"), (r"cc", "ml"), (r"錠", "tablet"), (r"滴", "drop"),
           (r"分(?!の)", "min"), (r"時間", "h"), (r"秒", "s"),
           (r"日", "day"), (r"週間?", "week"), (r"[かカヶケ]月", "month"), (r"年", "year"),
           (r"cm|センチ", "cm"), (r"mm|ミリ(?!リットル|グラム)", "mm"), (r"km|キロ(?!グラム)", "km"),
           (r"m(?![lgL])|メートル", "m"), (r"kg|キログラム", "kg"), (r"mg|ミリグラム", "mg"),
           (r"g|グラム", "g"), (r"ml|mL|ミリリットル", "ml"), (r"[lL]|リットル", "l"),
           (r"%", "%"), (r"人", "person"), (r"回", "times")],
}
# Units where a number on one side only with the unit is an error (doses,
# temperatures): "5 mg" and 「5」 is not the same instruction.
CRITICAL = {"°C", "°F", "deg", "mcg", "mg", "g", "kg", "ml", "l", "%", "IU", "tablet", "drop", "mmHg",
            "tsp", "tbsp", "cup"}
TEMPERATURE = {"°C", "°F", "deg"}  # deg: English "degrees" without a scale
# Times that must not get lost next to a number ("Wait 5 minutes" / 「5待つ」).
SHORT_TIMES = {"min", "h", "s"}
# A rate after a unit: 0.1 mg/kg, 5 mg/kg/dose. Rates per time are read as
# numbers instead (per minute = 1 minute, /日 = 1日), so both languages agree.
RATE = {"en": r"\s*(?:/|per\s+)\s*(kg|kilograms?|dose)\b", "ja": r"\s*/\s*(kg|回)"}
RATE_NAMES = {"kilogram": "kg", "kilograms": "kg", "回": "dose"}
# Minus sign from words (マイナス, 氷点下, minus): always a sign.
MINUS = "−"
JA_COUNTERS = ("つ|回|分|時間|日|人|度|週間|か月|ヶ月|カ月|年|歳|倍|枚|本|個|件|段階|か所|箇所|錠|粒|滴|包|袋|杯"
               "|グラム|ミリ|キロ|リットル|メートル|センチ")


def _canon(n):
    return str(int(float(n))) if float(n).is_integer() else str(float(n))


def _normalize(text, lang):
    # NFKC makes º an "o", and m² or CO₂ a number
    t = unicodedata.normalize("NFKC", re.sub(r"[²³₂₃]", "", text.replace("º", "°")))
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)  # 3,122
    # Minus: −5℃, minus 5, negative 5, マイナス5, 氷点下5度, 零下5度, 5 degrees below zero
    t = re.sub(r"\b(?:minus|negative)\s+(?=\d)|(?:マイナス|氷点下|零下)\s?(?=\d)", MINUS, t, flags=re.I)
    t = re.sub(r"(?<![\d.-])(\d+(?:\.\d+)?)(\s*(?:°\s?[CF]\b|degrees?(?: (?:celsius|fahrenheit|c|f)\b)?)?)\s+below zero\b",
               MINUS + r"\1\2", t, flags=re.I)
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
        # per day = 1日, 5 mg daily = 5 mg 1日, per minute = 1分
        t = re.sub(r"(?:/\s*|\bper\s+)(day|person|minute|min|hour|hr|h|second|sec|s|week)\b"
                   r"|(?<=times) (?:daily|a day)\b|(?<=2 times) daily\b"
                   r"|(?<=\d)(?= ?[a-zA-Z%]{0,12} (?:daily|every day|each day)\b)",
                   lambda m: f" 1 {m[1] or 'day'} " if m[0] else "", t, flags=re.I)
        t = re.sub(r"(?<=\d)( ?[a-zA-Z%]{0,12}) (?:daily|every day|each day)\b", r"\1 1 day", t, flags=re.I)
        t = re.sub(r"\bevery hour\b|\bhourly\b", " 1 hour ", t, flags=re.I)
        t = re.sub(r"\b(" + TENS + r")[- ](" + ONES + r")\b",
                   lambda m: str(WORDS[m[1].lower()] + ONE_TO_NINE[m[2].lower()]), t, flags=re.I)
        t = re.sub(r"\ba dozen\b", "12", t, flags=re.I)
        # Reiwa 8 (2026): one year, not two numbers.
        # (a wrong year in brackets stays, so it shows as a difference)
        t = re.sub(r"\b(Reiwa|Heisei|Showa) (\d+)(?: \((\d{4})\))?", _era, t, flags=re.I)
        # January 1 = 1月1日; a month name only next to a number ("March to
        # the shelter" is not a date)
        names = "|".join(m.capitalize() for m in MONTHS)
        t = re.sub(r"\b(" + names + r")\b(?: (\d{1,2})(?:st|nd|rd|th)?\b(?!,? \d{4}\b(?! )))?", _month, t)
        t = re.sub(r"(?<!than )\b(?:one|a single)(?= (?:hour|minute|second|day|week|month|year|person|time|tablet|pill"
                   r"|drop|dose|gram|milligram|liter|litre|cup|meter|metre|kilogram|unit)s?\b)", "1", t, flags=re.I)
        t = re.sub(r"\bthe first one\b", "1", t, flags=re.I)
        # the second (2), not "a second" (time) or "every second"
        t = re.sub(r"(?<!every )(?<!\ba )(?<!\d )\bsecond\b(?! of)|\b(?:third|fourth|fifth)\b(?! of)",
                   lambda m: str(["second", "third", "fourth", "fifth"].index(m[0].lower()) + 2), t)
        t = re.sub(r"\b(" + "|".join(WORDS) + r")\b", lambda m: str(WORDS[m[1].lower()]), t, flags=re.I)
    else:
        t = re.sub(r"(令和|平成|昭和)(元|\d+)年",
                   lambda m: f"{ERAS[m[1]] + (1 if m[2] == '元' else int(m[2]))}年", t)
        # 令和6年（2024年）: one year
        t = re.sub(r"(\d{4})年(度|中)?\(\1年?(?:度|中)?\)", r"\1年\2", t)
        # 二つ, 五分, 二十分, 四輪 (not 十分 "enough", not 二度と "never again")
        t = re.sub(r"(?<![一-鿿])([二三四五六七八九]?十[一二三四五六七八九]?|[二三四五六七八九])(?=" + JA_COUNTERS + "|輪)",
                   _kanji, t)
        t = re.sub(r"三が日", "3日間", t)
        # 大さじ2 = 2 tbsp; /日 = 1日; 毎日5mg = 1日5mg
        t = re.sub(r"([大小])さじ\s?(\d+(?:\.\d+)?|[一二三四五六七八九十])",
                   lambda m: f"{KANJI.get(m[2], m[2])} {'tbsp' if m[1] == '大' else 'tsp'}", t)
        t = re.sub(r"\s*/\s*(日|分|時間|秒|週)", r" 1\1", t)
        t = re.sub(r"毎日(?=[^。\n]{0,6}\d)|(?<=\d)(\s?[a-zA-Z%]+)\s?毎日", lambda m: f"{m[1] or ''} 1日", t)
        # 時速200km = 200 km/h = 200 km 1時間
        t = re.sub(r"([時分秒])速\s?(\d+(?:\.\d+)?)\s?(km|キロ(?:メートル)?|m(?![lgL])|メートル)",
                   lambda m: f"{m[2]}{'km' if m[3].startswith(('k', 'キ')) else 'm'} 1"
                             f"{ {'時': '時間', '分': '分', '秒': '秒'}[m[1]]}", t)
        # 10万, 19万9千, 8割 (= 80 %)
        t = re.sub(r"(\d+(?:\.\d+)?)万(?:(\d+)千)?",
                   lambda m: _canon(float(m[1]) * 10000 + int(m[2] or 0) * 1000), t)
        t = re.sub(r"(\d+)千(?!葉)", lambda m: str(int(m[1]) * 1000), t)
        t = re.sub(r"(\d+)割", lambda m: f"{int(m[1]) * 10}%", t)
    return t


ONE_TO_NINE = {w: i + 1 for i, w in enumerate(ONES.split("|"))}


def _era(m):
    year = ERAS[m[1].lower()] + int(m[2])
    return f" {year} " + (f"{m[3]} " if m[3] and int(m[3]) != year else "")


def _month(m):
    near = m.string[m.end():m.end() + 8]
    before = m.string[max(0, m.start() - 4):m.start()]
    names = "|".join(x.capitalize() for x in MONTHS)
    wider = m.string[max(0, m.start() - 14):m.start()]
    dated = (m[2] or re.match(r",?\s+\d", near) or re.search(r"\d\s*$", before)
             or re.match(r"\s*(?:to|through|until|-|–|and)\s+(?:" + names + ")", m.string[m.end():])
             or re.search(r"(?:\b(?:in|from|until|since|by|early|late|mid)\s*-?|(?:" + names + r")\s+(?:to|through|-|–|and)\s+)$",
                          wider))
    if not dated:
        return m[0]
    return f" {MONTHS.index(m[1].lower()) + 1} month " + (f"{m[2]} day " if m[2] else "")


def _kanji(m):
    k, after = m[1], m.string[m.end():m.end() + 2]
    if (k == "十" and after.startswith("分") and after != "分間") or after == "度と":
        return k
    if "十" in k:
        tens, _, ones = k.partition("十")
        return str(KANJI.get(tens, 1) * 10 + KANJI.get(ones, 0))
    return str(KANJI[k])


def numbers(text, lang):
    """Counter of numbers, and for each number the units found after it."""
    t = _normalize(text, lang)
    found, units, order = Counter(), {}, []
    for m in re.finditer(r"(?<![\d.])([-−]?)(\d+(?:\.\d+)?|\.\d+)", t):
        # − (from マイナス, 氷点下, minus) is a sign; "-" is one unless it
        # joins two words or numbers (3-5, TC 4-02, COVID-19), so 「外は-5℃」 keeps it.
        before = t[m.start() - 1] if m.start() else " "
        sign = "-" if m[1] == MINUS or (m[1] == "-" and not (before.isascii() and before.isalnum())) else ""
        n = sign + _canon(m[2])
        found[n] += 1
        rest = t[m.end():m.end() + 40].lstrip()
        flags = re.I if lang == "en" else 0
        # 15 to 30 minutes, 3〜5分: the unit after the range is for both numbers
        span = re.match(r"(?:to|-|–|~|〜|から)\s*\d+(?:\.\d+)?\s*", rest, flags)
        if span:
            rest = rest[span.end():]
        unit = ""
        for pattern, name in UNITS[lang]:
            u = re.match(pattern, rest, flags)
            if u:
                unit, rest = name, rest[u.end():]
                break
        for _ in range(2):  # mg/kg/dose
            rate = re.match(RATE[lang], rest, flags) if unit else None
            if not rate:
                break
            per = rate[1].lower()
            unit += "/" + RATE_NAMES.get(per, per)
            rest = rest[rate.end():]
        if unit:
            units.setdefault(n, Counter())[unit] += 1
            order.append(n)
    return found, units, order


def check_numbers(src, src_lang, dst, dst_lang):
    a, au, _ = numbers(src, src_lang)
    b, bu, _ = numbers(dst, dst_lang)
    flags = []
    for n in sorted(set(a) | set(b), key=lambda x: float(x)):
        if a[n] > b[n]:
            flags.append((n, f"{n} in the original{_times(a[n] - b[n])}, missing in the translation"))
        elif b[n] > a[n]:
            flags.append((n, f"{n} in the translation{_times(b[n] - a[n])}, not in the original"))
        elif _units_differ(au.get(n, Counter()), bu.get(n, Counter())):
            ua, ub = au.get(n, Counter()), bu.get(n, Counter())
            flags.append((n, f"{n} has unit {_names(ua)} in the original, {_names(ub)} in the translation"))
    return flags


def check_order(src, src_lang, dst, dst_lang):
    """Numbers with a unit in another order ("30 presses, then 2 breaths" /
    「2回押して30回息を」). Word order differs between languages, so this is a
    warning; numbers that appear more than once are left out."""
    _, _, x = numbers(src, src_lang)
    _, _, y = numbers(dst, dst_lang)
    once = {n for n in set(x) & set(y) if x.count(n) == 1 and y.count(n) == 1}
    x, y = [n for n in x if n in once], [n for n in y if n in once]
    if x != y:
        return [("order", f"numbers with units in another order: {', '.join(x)} in the original, "
                          f"{', '.join(y)} in the translation")]
    return []


def _base(unit):
    return unit.split("/")[0]


def _units_differ(x, y):
    """Units after the same number on both sides. Doses and temperatures
    (with their rate, mg/kg) must match one for one; other units only
    when one side uses a unit the other does not (5 minutes / 5時間)."""
    tx = sum(k for u, k in x.items() if _base(u) in TEMPERATURE)
    ty = sum(k for u, k in y.items() if _base(u) in TEMPERATURE)
    sx, sy = {_base(u) for u in x} & {"°C", "°F"}, {_base(u) for u in y} & {"°C", "°F"}
    if tx != ty or (sx and sy and sx != sy):
        return True
    # A short time on one side only: "Wait 5 minutes" / 「5待つ」
    if sum(k for u, k in x.items() if u in SHORT_TIMES) != sum(k for u, k in y.items() if u in SHORT_TIMES) \
            and not ({u for u in x} & SHORT_TIMES and {u for u in y} & SHORT_TIMES):
        return True
    crit = {u for u in x | y if _base(u) in CRITICAL - TEMPERATURE}
    if any(x[u] != y[u] for u in crit):
        return True
    rates = {u for u in x | y if "/" in u and _base(u) not in TEMPERATURE}
    if any(x[u] != y[u] for u in rates):
        return True
    bx = {_base(u) for u in x} - CRITICAL
    by = {_base(u) for u in y} - CRITICAL
    return bool(bx - by) and bool(by - bx)


def _names(units):
    return "/".join(sorted(units)) or "none"


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
    name = Path(path).name
    if not name.endswith(".json") or name.count(".") < 2:
        sys.exit(f"{path}: expected packages/translations/<id>.<lang>.json")
    pid, dst = name[:-len(".json")].rsplit(".", 1)
    manifest = ROOT / "packages" / f"{pid}.json"
    src = json.loads(manifest.read_text(encoding="utf-8")).get("language", "ja") if manifest.exists() else "ja"
    if {src, dst} != {"en", "ja"}:
        sys.exit(f"{path}: only English and Japanese are supported ({src} → {dst})")
    return pid, src, dst


def check_entry(key, e, src_lang, dst_lang, terms):
    """Flags of one passage: [(check, item, message, level)], and the exceptions not used."""
    flags = []
    if not isinstance(e, dict):
        return [("integrity", "entry", "entry is not an object", "error")], []
    src, text = e.get("source"), e.get("text")
    if not isinstance(text, str):
        text = ""
    accept = e.get("accept", [])
    if not isinstance(accept, list) or not all(isinstance(a, dict) for a in accept):
        return [("integrity", "accept", "\"accept\" must be a list of {check, item, reason}", "error")], []
    if not isinstance(src, str) or not src:
        return [("integrity", "source", "no original (\"source\") stored next to the translation", "error")], []
    if passage_key(src) != key:
        flags.append(("integrity", "key", f"key {key} is not the hash of the stored original "
                                          f"({passage_key(src)})", "error"))
    if not text.strip():
        flags.append(("integrity", "text", "translation is empty", "error"))
    flags += [("numbers", n, msg, "error") for n, msg in check_numbers(src, src_lang, text, dst_lang)]
    flags += [("words", g, msg, "warning") for g, msg in check_words(src, src_lang, text, dst_lang, terms)]
    flags += [("numbers", g, msg, "warning") for g, msg in check_order(src, src_lang, text, dst_lang)]
    return apply_accept(flags, accept, text)


def apply_accept(flags, accept, text):
    """Applies a person's exceptions to the flags of one translation. An
    exception counts only with a reason, and only for the translation text
    it was made for ("for": its hash), so editing the translation brings
    the flag back."""
    current = passage_key(text)
    accepted, outdated = {}, []
    for a in accept:
        k = (str(a.get("check")), str(a.get("item")))
        if a.get("for") == current:
            accepted[k] = str(a.get("reason") or "")
        else:
            outdated.append(k)
    out = []
    for check, item, msg, level in flags:
        reason = accepted.get((check, item))
        if reason is not None and check != "integrity" and reason.strip():
            out.append((check, item, f"{msg} — accepted: {reason}", "accepted"))
        else:
            if level != "warning" and check != "integrity":
                msg += f" (to accept: {{\"check\": \"{check}\", \"item\": \"{item}\", \"for\": \"{current}\", \"reason\": ...}})"
            out.append((check, item, msg, level))
    used = {(c, i) for c, i, _, lvl in out if lvl == "accepted"}
    unused = [f"{c} {i}: no such flag any more; remove it" for c, i in accepted if (c, i) not in used]
    unused += [f"{c} {i}: made for another translation text; check again and set \"for\": \"{current}\""
               for c, i in outdated]
    return out, unused


def check_heading(orig, tr, accept, src_lang, dst_lang):
    if not isinstance(tr, str) or not tr.strip():
        return [("integrity", "text", "heading translation is missing or not text", "error")], []
    if not isinstance(accept, list) or not all(isinstance(a, dict) for a in accept):
        return [("integrity", "accept", "\"accept_headings\" entries must be lists of {check, item, for, reason}",
                 "error")], []
    flags = [("numbers", n, msg, "error") for n, msg in check_numbers(orig, src_lang, tr, dst_lang)]
    return apply_accept(flags, accept, tr)


def check_file(path, report_dir=REPORTS, quiet=False):
    """Checks one translation file and writes its report. Returns (errors, warnings)."""
    path = Path(path)
    pid, src_lang, dst_lang = langs(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("passages"), dict) or not isinstance(data.get("headings", {}), dict):
        sys.exit(f"{path}: needs \"passages\" and \"headings\" objects")
    terms = load_terms(src_lang, dst_lang)
    if not terms and not quiet:
        print(f"{path.name}: no word list terms.{src_lang}-{dst_lang}.json: word checks are off")
    results = []
    for key, e in data["passages"].items():
        flags, unused = check_entry(key, e, src_lang, dst_lang, terms)
        if flags or unused:
            results.append((key, e, flags, unused))
    heading_flags = []
    accept_h = data.get("accept_headings", {})
    if not isinstance(accept_h, dict):
        sys.exit(f"{path}: \"accept_headings\" must be an object")
    for orig, tr in data.get("headings", {}).items():
        flags, unused = check_heading(orig, tr, accept_h.get(orig, []), src_lang, dst_lang)
        if flags or unused:
            heading_flags.append((orig, tr, flags, unused))
    for orig in accept_h:
        if orig not in data.get("headings", {}):
            heading_flags.append((orig, "", [], ["heading no longer in the file; remove its exceptions"]))
    errors = sum(1 for _, _, f, _ in results + heading_flags for x in f if x[3] == "error")
    warnings = sum(1 for _, _, f, _ in results for x in f if x[3] == "warning")
    reviewed = sum(1 for e in data["passages"].values() if isinstance(e, dict) and e.get("reviewed"))
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
        for key, _, _, unused in results:
            for u in unused:
                print(f"  outdated exception {key} {u}")
        for orig, _, _, unused in heading_flags:
            for u in unused:
                print(f"  outdated exception heading {orig!r} {u}")
        for orig, _, flags, _ in heading_flags:
            for check, item, msg, level in flags:
                if level == "error":
                    print(f"  error heading {orig!r} {check}: {msg}")
    return errors, warnings


def _rel(p):
    try:
        return Path(p).resolve().relative_to(ROOT)
    except ValueError:
        return p


def _text(x):
    return x if isinstance(x, str) else ""


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
        for o, t, flags, unused in heading_flags:
            lines += [f"- `{o}` → `{t}`"]
            lines += [f"  - **{level}** {check}: {msg}" for check, _, msg, level in flags]
            lines += [f"  - **outdated exception** {u}" for u in unused]
        lines.append("")
    order = {"error": 0, "warning": 1, "accepted": 2}
    results = sorted(results, key=lambda r: min((order[x[3]] for x in r[2]), default=3))
    for key, e, flags, unused in results:
        e = e if isinstance(e, dict) else {}
        state = "reviewed" if e.get("reviewed") else "not reviewed"
        lines += [f"## {key} ({state})", ""]
        lines += [f"- **{level}** {check}: {msg}" for check, _, msg, level in flags]
        lines += [f"- **outdated exception** {u}" for u in unused]
        lines += ["", "Original:", "", _quote(_text(e.get("source"))), "",
                  "Translation:", "", _quote(_text(e.get("text"))), ""]
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
        e = e if isinstance(e, dict) else {}
        print(f"  no longer in the source: {k} {_first(_text(e.get('source')) or _text(e.get('text')))}")
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
