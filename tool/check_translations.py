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
           (r"°|degrees?\b", "deg"), (r"mcg\b|µg\b|μg\b|ug\b|micrograms?\b", "mcg"), (r"mmhg\b", "mmHg"),
           (r"iu\b|international units?\b|units?\b", "IU"), (r"cc\b", "ml"), (r"(?:tablet|pill|capsule)s?\b", "tablet"),
           (r"drops?\b", "drop"), (r"gm\b", "g"), (r"tsp\b|teaspoons?\b", "tsp"),
           (r"tbsp\b|tablespoons?\b", "tbsp"), (r"cups?\b|cupfuls?\b|glass(?:es)?\b|mugs?\b|bowls?\b", "cup"), (r"spoonfuls?\b", "tsp"),
           (r"min(?:ute)?s?\b", "min"), (r"h(?:ou)?rs?\b|hours?\b|h\b", "h"), (r"sec(?:ond)?s?\b", "s"),
           (r"days?\b", "day"), (r"weeks?\b", "week"), (r"months?\b", "month"), (r"years?\b", "year"),
           (r"cm\b|centimet(?:er|re)s?\b", "cm"), (r"mm\b|millimet(?:er|re)s?\b", "mm"),
           (r"km\b|kilomet(?:er|re)s?\b", "km"), (r"m\b|met(?:er|re)s?\b", "m"),
           (r"kg\b|kilograms?\b", "kg"), (r"mg\b|milligrams?\b", "mg"), (r"g\b|grams?\b", "g"),
           (r"ml\b|millilit(?:er|re)s?\b", "ml"), (r"l\b|lit(?:er|re)s?\b", "l"),
           (r"%|percent\b|per cent\b", "%"), (r"people\b|persons?\b", "person"),
           (r"times\b", "times")],
    # 度 alone is Celsius in Japanese (or an angle, which English writes as degrees)
    "ja": [(r"dayish", "dayish"), (r"℃|°C", "°C"), (r"°F", "°F"), (r"°|度(?!目|に分け|(?:(?:と|および|または|もしくは|か|、|~|〜|-)\d+度)?の?(?:熱傷|火傷|やけど))", "°C"), (r"tsp", "tsp"), (r"tbsp", "tbsp"),
           (r"カップ|cup", "cup"), (r"mcg|µg|μg|ug|マイクログラム", "mcg"),
           (r"mmHg", "mmHg"), (r"IU|単位", "IU"), (r"cc", "ml"), (r"錠|カプセル", "tablet"), (r"杯", "cup"), (r"滴", "drop"),
           (r"分(?!の)", "min"), (r"時間", "h"), (r"秒", "s"),
           (r"日", "day"), (r"週間?", "week"), (r"[かカヶケ]月", "month"), (r"年", "year"),
           (r"cm|センチ", "cm"), (r"mm|ミリ(?!リットル|グラム)", "mm"), (r"km|キロメートル", "km"), (r"キロ(?!グラム)", "kilo"),
           (r"m(?![lgL])|メートル", "m"), (r"kg|キログラム", "kg"), (r"mg|ミリグラム", "mg"),
           (r"g|グラム", "g"), (r"ml|mL|ミリリットル", "ml"), (r"[lL]|リットル", "l"),
           (r"%|パーセント", "%"), (r"人", "person"), (r"回", "times")],
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
RATE = {"en": r"\s*(?:/|per\s+)\s*(kg|kilograms?|dose|ml|millilit(?:er|re)s?|dl|l|lit(?:er|re)s?|m2|m|cm)\b",
        "ja": r"\s*/\s*(kg|回|dose|ml|dl|l|m2|cm|m)(?![a-zA-Z0-9])"}
RATE_NAMES = {"kilogram": "kg", "kilograms": "kg", "回": "dose", "milliliter": "ml", "milliliters": "ml",
              "millilitre": "ml", "millilitres": "ml", "liter": "l", "liters": "l", "litre": "l", "litres": "l"}
# Minus sign from words (マイナス, 氷点下, minus): always a sign.
MINUS = "−"
JA_COUNTERS = ("つ|回|分|時間|日|人|度|週間|か月|ヶ月|カ月|年|歳|倍|枚|本|個|件|段階|か所|箇所|錠|粒|滴|包|袋|杯"
               "|グラム|ミリ|キロ|リットル|メートル|センチ")


def _canon(n):
    return str(int(float(n))) if float(n).is_integer() else str(float(n))


def _normalize(text, lang):
    # NFKC makes º an "o", and m² or CO₂ a number
    # (mg/m² keeps its area: m2; ㎡ is m²)
    text = text.replace("º", "°").replace("㎡", "m²").replace("㎥", "m³")
    t = unicodedata.normalize("NFKC", re.sub(r"(?<!/m)[²³₂₃]", "", text))
    t = re.sub(r"\d{40,}", lambda m: m[0][:40], t)  # no real number is this long; keeps the regexes fast
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)  # 3,122
    t = re.sub(r"(?<=\d)(\s?m)[23](?!\d)", r"\1", t)  # 3 m2 = 3㎡
    t = re.sub(r"\bCO2\b", "CO", t)
    # Dashes as minus or range: –5 °C, 3–5. (Joining dashes are told apart in numbers().)
    t = re.sub(r"[‐‑‒–—]|(?<![ァ-ヶー])ー(?=\d)", "-", t)
    # Figure references, which translations may leave out.
    t = re.sub(r"\((?:see )?(?:figure|table) [\d-]+(?: on page \d+)?\.?\)|[(]図\s?[\d-]+[)]", " ", t, flags=re.I)
    if lang == "en":
        # Paragraph numbers, chapter lines, page footers and figure titles
        # of the US manuals ("16-40. In the event of ...", "50 TC 21-3").
        t = re.sub(r"(?m)^\s*\d+-\d+\.\s", " ", t)
        t = re.sub(r"(?m)^(?:Chapter \d+|\d+ (?:TC|ATP) [\d.-]+|(?:TC|ATP) [\d.-]+ \d+|Figure \d+-\d+\..*)$",
                   " ", t)
        t = re.sub(r"(?<=\d)-(?=(?:second|minute|hour|day|week|month|year|meter|metre|liter|litre|mile|kilometer|km|kg|mg|mcg|g|ml|l"
                   r"|tablet|cup|iu|unit)s?\b)",
                   " ", t, flags=re.I)  # a 10-minute walk
        t = re.sub(r"\bonce or twice\b", "1 or 2 times", t, flags=re.I)
        t = re.sub(r"\btwice\b", "2 times", t, flags=re.I)
        t = re.sub(r"\bonce(?= (?:an?|per|every) (?:\d+ )?(?:days?|weeks?|months?|years?|hours?|minutes?)\b| daily\b)",
                   "1 times", t, flags=re.I)
        # Frequencies and rates are "1 per-day" (not a duration "1 day"):
        # per day, a day (after a dose), every day, daily = 1日あたり, 毎日;
        # every other day = 隔日
        t = re.sub(r"\bevery other (day|week)\b", lambda m: f" 2 per-{m[1].lower()} ", t, flags=re.I)
        t = re.sub(r"\b24 hours a day\b", "24 hours", t, flags=re.I)  # = 24時間体制
        t = re.sub(r"(?:/\s*|\bper\s+)(day|person|minute|min|hour|hr|h|second|sec|s|week|month|year)\b"
                   r"|\b(?:every|each)\s+(day|minute|hour|week|month|year)\b",
                   lambda m: f" 1 per-{_per(m[1] or m[2])} ", t, flags=re.I)
        t = re.sub(r"\ban? (tablet|pill|capsule|dose|drop|glass|cup|mug|bowl|spoonful)\b", r"1 \1", t, flags=re.I)
        t = re.sub(r"\bone(?= (?:or|to) (?:two|three|four|five|\d))", "1", t, flags=re.I)
        t = re.sub(r"\b(\d+) to (\d+)(?= (?:dilution|ratio|solution|mix))", r"\1:\2", t, flags=re.I)
        t = re.sub(r"(\btimes|\btwice|\bonce|\d+(?:\.\d+)?\s?(?:[a-z%]{1,10}s?)?(?: of \w+)?) an? (day|week|month|year|hour|minute)\b",
                   lambda m: f"{m[1]} 1 per-{_per(m[2])} ", t, flags=re.I)
        t = re.sub(r"\b(daily|weekly|monthly|hourly|yearly|annually|nightly)\b"
                   r"(?! (?:life|lives|living|use|routine|activities))",
                   lambda m: f" 1 per-{_per(FREQ[m[1].lower()])} ", t, flags=re.I)
        # "at a time", "each time" = 1回, 毎回: no number on either side
        t = re.sub(r"\b(?:at a|per|each|every) (?:time|dose)\b", " ", t, flags=re.I)
        t = re.sub(r"\b(?:every|each) (night|morning|evening|year)\b",
                   lambda m: f" 1 per-{_per(m[1])} ", t, flags=re.I)
        t = re.sub(r"\b(" + TENS + r")[- ](" + ONES + r")\b",
                   lambda m: str(WORDS[m[1].lower()] + ONE_TO_NINE[m[2].lower()]), t, flags=re.I)
        t = re.sub(r"\ba dozen\b", "12", t, flags=re.I)
        # "half an hour", "an hour or two"; "for a day" is a duration, "2 liters a day" a rate
        t = re.sub(r"\bhalf an hour\b", "30 minutes", t, flags=re.I)
        t = re.sub(r"\ban? (hour|minute|day|week|month|year) or two\b", r"1 to 2 \1s", t, flags=re.I)
        t = re.sub(r"\ban? (hour|minute|day|week|month|year)\b", r"1 \1", t, flags=re.I)
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
        # second-degree burn = 2度の熱傷; first- and second-degree = 1度と2度
        t = re.sub(r"\b(first|second|third)-?(?=,? (?:(?:second|third)-?,? )*(?:and|or|to) (?:first|second|third|fourth)[- ]degree\b)",
                   lambda m: f"{['first', 'second', 'third'].index(m[1].lower()) + 1} grade", t, flags=re.I)
        t = re.sub(r"\b(first|second|third|fourth)[- ]degree\b",
                   lambda m: f"{['first', 'second', 'third', 'fourth'].index(m[1].lower()) + 1} grade", t, flags=re.I)
        # the second (2), not "a second" (time) or "every second"
        t = re.sub(r"(?<!every )(?<!\ba )(?<!\d )\bsecond\b(?! of)|\b(?:third|fourth|fifth)\b(?! of)",
                   lambda m: str(["second", "third", "fourth", "fifth"].index(m[0].lower()) + 2), t)
        t = re.sub(r"\b(" + "|".join(WORDS) + r")\b", lambda m: str(WORDS[m[1].lower()]), t, flags=re.I)
        t = re.sub(r"(?<!below )\bzero\b", "0", t, flags=re.I)
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
        # Frequencies and rates: /日, 毎日, 1日3回, 1日あたり, 1分間に = 1 per-…;
        # 隔日 = every other day
        t = re.sub(r"隔日|(?<![\d.])1日おき", " 2 per-day ", t)
        t = re.sub(r"隔週|(?<![\d.])1週間?おき", " 2 per-week ", t)
        t = re.sub(r"一(?=日\s?\d|回\s?\d)|(?<=日)一(?=回)", "1", t)
        t = re.sub(r"(?<![\d.])1日(?=量|の(?:上限|量|摂取量|最大|最高))", " 1 per-day ", t)
        # In a sentence about burns, N度 is a grade (1度、2度、3度の熱傷; 2度以上のやけど)
        t = re.sub(r"[^。\n]*(?:熱傷|火傷|やけど)[^。\n]*",
                   lambda m: re.sub(r"(\d+)\s?度", r"\1級", m[0]), t)
        # 度 as "times": 1日2度, 週3度, 2度塗る
        t = re.sub(r"(?<=[日週月])(\d+)度", r"\1回", t)
        t = re.sub(r"(\d+)度(?=塗|繰り返|行|洗|拭|測|飲|服用|吸|使|かけ)", r"\1回", t)
        # 華氏98.6度 = 98.6 °F; 摂氏 = °C; Ⅱ度熱傷 (NFKC: II度) = 2度
        t = re.sub(r"華氏\s?(-?\d+(?:\.\d+)?)\s?度", r"\1°F", t)
        t = re.sub(r"摂氏\s?(?=-?\d)", "", t)
        t = re.sub(r"(?<![A-Za-z])(IV|III|II|I)(?=度)", lambda m: str(["I", "II", "III", "IV"].index(m[1]) + 1), t)
        t = re.sub(r"\s*/\s*(日|分|時間|秒|週|人|月|min|h|s|day)(?![a-zA-Z])|\s*/\s*(日|分|時間|時|秒|週|人|月)"
                   r"|毎(日|週|月|時|分|秒|年|朝|晩|夜)", lambda m: f" 1 per-{_per(m[1] or m[2] or m[3])} ", t)
        # 1日30分 is "30 minutes a day" or "1 day 30 minutes": either, by the other side
        t = re.sub(r"(?<![\d.月])1日(?=\s?\d+(?:\.\d+)?\s?(?:秒|分|時間))", " 1 dayish ", t)
        # 3時30分, 3時半 = 3:30; 零度 = 0度
        t = re.sub(r"(?<![\d.])(\d{1,2})時(?:(\d{1,2})分|(半))", lambda m: f"{m[1]}:{m[2] or 30} ", t)
        t = re.sub(r"零(?=度|℃)|ゼロ", "0", t)
        # 1対1 = 1:1
        t = re.sub(r"(\d+(?:\.\d+)?)対(\d+(?:\.\d+)?)", r"\1:\2", t)
        # 週3回, 月に1回 = per week 3 times; 1回2錠 = 2 tablets per time
        t = re.sub(r"(?<![\d.毎一-鿿かカヶケ])(週|月|年)に?(?=\d+(?:\.\d+)?\s?(?:回|日))", lambda m: f" 1 per-{_per(m[1])} ", t)
        # (only before a dose: 1日1回5mg keeps its "once")
        t = re.sub(r"(?<![\d.第一日週月にでと])1回(?:に|あたり|につき)?"
                   r"(?=\d+(?:\.\d+)?\s?(?:錠|カプセル|滴|包|粒|mg|g|ml|cc|μg|mcg|ug|IU|単位|杯|tsp|tbsp))|毎回", " ", t,
                   flags=re.I)
        t = re.sub(r"(?<![\d.月])1\s?(日|分間?|時間|週間?|人|[かカヶケ]月|年)\s?(?:あたり|当たり|につき|ごと|に(?!は)|(?=1\s?日\s?\d)|(?=\d+(?:\.\d+)?\s?(?![\d.]|秒|分|時間|日|週|か月|ヶ月|カ月|年)))",
                   lambda m: f" 1 per-{_per(m[1])} ", t)
        # 時速200km = 200 km/h = 200 km 1時間
        t = re.sub(r"([時分秒])速\s?(\d+(?:\.\d+)?)\s?(km|キロ(?:メートル)?|m(?![lgL])|メートル)",
                   lambda m: f"{m[2]}{'km' if m[3].startswith(('k', 'キ')) else 'm'} 1 per-"
                             f"{ {'時': 'h', '分': 'min', '秒': 's'}[m[1]]} ", t)
        # 10万, 19万9千, 8割 (= 80 %)
        t = re.sub(r"(\d+(?:\.\d+)?)万(?:(\d+)千)?",
                   lambda m: _canon(float(m[1]) * 10000 + int(m[2] or 0) * 1000), t)
        t = re.sub(r"(\d+)千(?!葉)", lambda m: str(int(m[1]) * 1000), t)
        t = re.sub(r"(\d+)割", lambda m: f"{int(m[1]) * 10}%", t)
    # Minus, after number words and kanji are digits: −5℃, minus five,
    # negative 5, マイナス五度, 氷点下5度, 零下5度, 5 degrees below zero
    t = re.sub(r"\b(?:minus|negative)\s+(?=\d)|(?:マイナス|氷点下|零下)\s?(?=\d)", MINUS, t, flags=re.I)
    t = re.sub(r"(?<![\d.-])(\d+(?:\.\d+)?)(\s*(?:°\s?[CF]\b|degrees?(?: (?:celsius|fahrenheit|c|f)\b)?)?)"
               r"\s+below zero\b", MINUS + r"\1\2", t, flags=re.I)
    return t


ONE_TO_NINE = {w: i + 1 for i, w in enumerate(ONES.split("|"))}
PER = {"day": "day", "日": "day", "person": "person", "人": "person", "minute": "min", "min": "min", "分": "min",
       "分間": "min", "hour": "h", "hr": "h", "h": "h", "時間": "h", "時": "h", "second": "s", "sec": "s", "s": "s",
       "秒": "s", "week": "week", "週": "week", "週間": "week", "month": "month", "月": "month",
       "か月": "month", "カ月": "month", "ヶ月": "month", "ケ月": "month"}


FREQ = {"daily": "day", "weekly": "week", "monthly": "month", "hourly": "hour", "yearly": "year",
        "annually": "year", "nightly": "night"}
PER.update({"dose": "time", "year": "year", "年": "year", "night": "night", "晩": "night", "夜": "night", "morning": "morning",
            "朝": "morning", "evening": "night", "time": "time", "回": "time"})


def _per(word):
    return PER.get(word.lower(), word.lower())


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
    if (k == "十" and after.startswith("分") and after != "分間") or after in ("度と", "度目"):
        return k
    if "十" in k:
        tens, _, ones = k.partition("十")
        return str(KANJI.get(tens, 1) * 10 + KANJI.get(ones, 0))
    return str(KANJI[k])


def numbers(text, lang):
    """Counter of numbers, and for each number the units found after it."""
    t = _normalize(text, lang)
    found, units, order = Counter(), {}, []
    # A ratio or a clock time is one value: 1:10 is not 10:1.
    # Not when a unit follows: 「手順1：5分」 is a label and 5 minutes;
    # "2:30 minutes" is read as 2 and 30 minutes.
    def ratio(m):
        after = t[m.end():m.end() + 12].lstrip()
        if any(re.match(p, after, re.I) for p, _ in UNITS[lang]):
            return m[0]
        found[f"{_canon(m[1])}:{_canon(m[2])}"] += 1
        return " "
    t = re.sub(r"(?<![\d.:])(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)(?!\d|\.\d|:\d)", ratio, t)
    for m in re.finditer(r"(?<![\d.])([-−]?)(\d+(?:\.\d+)?|\.\d+)", t):
        # − (from マイナス, 氷点下, minus) is a sign; "-" is one unless it
        # joins two words or numbers (3-5, TC 4-02, COVID-19), so 「外は-5℃」 keeps it.
        before = t[m.start() - 1] if m.start() else " "
        # after a counter, a range only when the same counter follows: 10日-15日, 5℃-10℃
        # (not 3日間-5℃ or 保管温度-18℃)
        after = t[m.end():m.end() + 3].lstrip()
        times = "秒分時間日週月年"
        joined = (before.isascii() and before.isalnum()) or (before in "日分時間秒週年月人回歳%℃度個錠"
                                                             and (before in after or (before in times
                                                                                      and after[:1] in times)))
        sign = "-" if m[1] == MINUS or (m[1] == "-" and not joined) else ""
        n = sign + _canon(m[2])
        found[n] += 1
        rest = t[m.end():m.end() + 40].lstrip()
        flags = re.I
        # 15 to 30 minutes, 3〜5分: the unit after the range is for both numbers
        span = re.match(r"(?:to|or|-|–|~|〜|から)\s*\d+(?:\.\d+)?\s*", rest, flags)
        if span:
            rest = rest[span.end():]
        unit = ""
        per = re.match(r"per-(\w+)", rest)
        for pattern, name in [] if per else UNITS[lang]:
            u = re.match(pattern, rest, flags)
            if u:
                unit, rest = name, rest[u.end():]
                break
        if per:
            unit, rest = per[0], rest[per.end():]
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
    # キロ alone is kg or km, 1日30分 a day or per day: take what the other side uses
    for x, y in ((au, bu), (bu, au)):
        for n, units in x.items():
            other = y.get(n, Counter())
            if units["kilo"]:
                units["kg" if other["kg"] else "km"] += units.pop("kilo")
            if units["dayish"]:
                units["per-day" if other["per-day"] else "day"] += units.pop("dayish")
    flags = []
    for n in sorted(set(a) | set(b), key=lambda x: (float(x.split(":")[0]), x)):
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
    # A frequency on one side only: "1 mL monthly" / 「1mLを1個」
    if sum(k for u, k in x.items() if u.startswith("per-")) != sum(k for u, k in y.items() if u.startswith("per-")):
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
