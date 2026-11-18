"""Tests for tool/check_translations.py.

    python3 -I -m unittest discover -s tool -p 'test_*.py'
"""
import json
import tempfile
import unittest
from pathlib import Path

import check_translations as ct

TERMS = ct.load_terms("en", "ja")

# The error that started this (lychnion/lychnion#1): the phone model turned
# "snug but not too tight" into 「しっかりと縛って」.
SNAKEBITE = ("• Apply a sterile bandage or dress over the bite, ensuring it is snug but not too tight. "
             "This can help\nimmobilize the area and reduce the spread of venom.")
SNAKEBITE_BAD = "・かまれた部分をしっかりと縛って固定してください。これは毒の広がりを抑えるのに役立ちます。"
SNAKEBITE_OK = ("・かまれた部分の上に、滅菌された包帯または被覆材を当てます。ぴったりと当てますが、きつく締めすぎないようにします。"
                "これは、その部分を動かないようにし、毒の広がりを抑えるのに役立つことがあります。")


def entry(source, text, **extra):
    return ct.passage_key(source), {"source": source, "text": text, "reviewed": False, **extra}


def flags(source, text, src="en", dst="ja", **extra):
    key, e = entry(source, text, **extra)
    return ct.check_entry(key, e, src, dst, TERMS)


class Snakebite(unittest.TestCase):
    def test_tie_firmly_is_flagged(self):
        found, _ = flags(SNAKEBITE, SNAKEBITE_BAD)
        items = {item for check, item, _, level in found if check == "words" and level == "warning"}
        self.assertIn("not-too-tight", items)  # the limit is lost
        self.assertIn("tight-firm", items)  # しっかり added
        self.assertIn("tie-bind", items)  # 縛る added

    def test_stored_translation_is_not_flagged(self):
        found, _ = flags(SNAKEBITE, SNAKEBITE_OK)
        items = {item for _, item, _, _ in found}
        self.assertFalse(items & {"not-too-tight", "tight-firm", "tie-bind"}, found)

    def test_lost_number_fails(self):
        found, _ = flags("• Rewarm the feet in water at 40°C for 15 to 30 minutes.",
                         "・足を40°Cのお湯で15分温めます。")
        errors = [(item, msg) for check, item, msg, level in found if level == "error"]
        self.assertEqual([item for item, _ in errors], ["30"])


class Numbers(unittest.TestCase):
    def nums(self, text, lang):
        return dict(ct.numbers(text, lang)[0])

    def test_same_numbers_in_other_writing(self):
        cases = [
            ("令和７年の山岳遭難は３，１２２件", "en", "Mountain accidents in Reiwa 7 (2025): 3,122"),
            ("最大で約19万9千人", "en", "up to about 199,000 people"),
            ("約８割", "en", "about 80%"),
            ("令和6年（2024年）には", "en", "in Reiwa 6 (2024)"),
            ("正月三が日（1月1日～3日）", "en", "The first 3 days of the New Year (January 1 to 3)"),
            ("四輪駆動車", "en", "four-wheel drive"),
            ("十分に休ませます", "en", "let them rest enough"),
        ]
        for ja, _, en in cases:
            with self.subTest(ja=ja):
                self.assertEqual(self.nums(ja, "ja"), self.nums(en, "en"))

    def test_english_manual_layout_is_ignored(self):
        en = ("16-40. In the event of a snakebite (see figure 4-1 on page 50.)\nChapter 4\n50 TC 21-3\n"
              "Change into dry socks at least twice daily.")
        self.assertEqual(self.nums(en, "en"), self.nums("少なくとも1日2回、乾いた靴下にはき替えます。", "ja"))

    def test_fahrenheit_with_ordinal_sign(self):
        self.assertEqual(self.nums("below 95ºF (35ºC)", "en"), {"95": 1, "35": 1})
        self.assertEqual(ct.numbers("below 95ºF (35ºC)", "en")[1]["95"], {"°F": 1})

    def test_unit_change_fails(self):
        found = ct.check_numbers("Keep the bandage on for 10 minutes.", "en", "包帯を10時間当てておきます。", "ja")
        self.assertEqual(len(found), 1)
        self.assertIn("unit min in the original, h in the translation", found[0][1])

    def test_dose_unit_change_with_space_fails(self):
        found = ct.check_numbers("Give 500 mg.", "en", "500 g を与えます。", "ja")
        self.assertEqual([n for n, _ in found], ["500"])
        found = ct.check_numbers("500 mg を与えます。", "ja", "Give 500 g.", "en")
        self.assertEqual([n for n, _ in found], ["500"])

    def test_minus_sign_is_kept(self):
        for ja in ("5℃以下", "5度以下"):
            with self.subTest(ja=ja):
                self.assertEqual([n for n, _ in ct.check_numbers("below -5°C", "en", ja, "ja")], ["-5", "5"])
        self.assertEqual(ct.check_numbers("below minus 5°C", "en", "−5℃以下", "ja"), [])
        self.assertEqual(ct.check_numbers("below -5°C", "en", "氷点下5度以下", "ja"), [])
        self.assertEqual(ct.check_numbers("3-5 minutes; TC 4-02.3", "en", "3〜5分。TC 4-02.3", "ja"), [])

    def test_critical_unit_on_one_side_fails(self):
        self.assertEqual(len(ct.check_numbers("Give 5 mg.", "en", "5を与えます。", "ja")), 1)
        self.assertEqual(len(ct.check_numbers("Give 20 mcg.", "en", "20 mgを与えます。", "ja")), 1)
        self.assertEqual(len(ct.check_numbers("for 3 h", "en", "3分間", "ja")), 1)
        self.assertEqual(ct.check_numbers("30 degrees", "en", "30度", "ja"), [])

    def test_kanji_minutes(self):
        self.assertEqual(ct.check_numbers("Wait 5 minutes, then 20 minutes.", "en", "五分待ち、次に二十分待ちます。", "ja"), [])
        self.assertEqual(ct.check_numbers("Rest enough. Never again.", "en", "十分に休み、二度としません。", "ja"), [])

    def test_same_number_with_two_units(self):
        src = "Give 5 mg and wait 5 minutes."
        self.assertEqual(ct.check_numbers(src, "en", "5 mgを与え、5分待ちます。", "ja"), [])
        for ja in ("5 mgを与え、5時間待ちます。", "5を与え、5分待ちます。"):
            with self.subTest(ja=ja):
                self.assertEqual([n for n, _ in ct.check_numbers(src, "en", ja, "ja")], ["5"])

    def test_rates(self):
        self.assertEqual(len(ct.check_numbers("Give 0.1 mg/kg.", "en", "0.1 mgを与えます。", "ja")), 1)
        self.assertEqual(len(ct.check_numbers("Give 5 mg per kg.", "en", "5 mgを与えます。", "ja")), 1)
        self.assertEqual(len(ct.check_numbers("Press 100 per minute.", "en", "100回/時間押します。", "ja")), 1)
        self.assertEqual(ct.check_numbers("時速200kmで流れる", "ja", "flows at 200 km/h", "en"), [])
        self.assertEqual(ct.check_numbers("時速200kmで流れる", "ja", "flows at 200 km per hour", "en"), [])

    def test_below_zero(self):
        self.assertEqual(len(ct.check_numbers("5 degrees below zero", "en", "5度", "ja")), 2)
        self.assertEqual(ct.check_numbers("5 degrees below zero", "en", "氷点下5度", "ja"), [])
        self.assertEqual(ct.check_numbers("negative 5°C", "en", "零下5℃", "ja"), [])

    def test_doses_in_words(self):
        self.assertEqual(len(ct.check_numbers("Take one tablet.", "en", "二錠飲みます。", "ja")), 2)
        self.assertEqual(ct.check_numbers("Take one tablet.", "en", "1錠飲みます。", "ja"), [])
        self.assertEqual(len(ct.check_numbers("Take 5 tablets.", "en", "5飲みます。", "ja")), 1)

    def test_wrong_year_in_brackets(self):
        self.assertEqual(len(ct.check_numbers("令和8年", "ja", "Reiwa 8 (2020)", "en")), 1)

    def test_month_names(self):
        self.assertEqual(ct.check_numbers("March to the shelter.", "en", "避難所へ向かいます。", "ja"), [])
        self.assertEqual(ct.check_numbers("例年1~3月に集中", "ja", "mostly from January to March", "en"), [])
        self.assertEqual(ct.check_numbers("Wait 1 second.", "en", "1秒待ちます。", "ja"), [])

    def test_swapped_numbers_warn(self):
        self.assertEqual(ct.check_order("Give 5 mg of A, then 10 mg of B.", "en", "Aを10 mg、Bを5 mg与えます。", "ja"),
                         [("order", "numbers with units in another order: 5, 10 in the original, "
                                    "10, 5 in the translation")])
        self.assertEqual(ct.check_order("Give 5 mg of A, then 10 mg of B.", "en", "Aを5 mg、Bを10 mg与えます。", "ja"), [])

    def test_japanese_minus_after_a_word(self):
        # The sign follows は or が in normal Japanese; it must not be lost.
        for ja in ("気温は氷点下5℃", "気温はマイナス5℃です", "外は-5℃", "外は−5℃", "気温が零下5度"):
            with self.subTest(ja=ja):
                self.assertEqual([n for n, _ in ct.check_numbers("The temperature is 5 °C", "en", ja, "ja")],
                                 ["-5", "5"])
                self.assertEqual(ct.check_numbers("The temperature is -5 °C", "en", ja, "ja"), [])
        self.assertEqual(len(ct.check_numbers("気温は氷点下5度", "ja", "The temperature is 5 degrees", "en")), 2)
        self.assertEqual(ct.check_numbers("COVID-19 and 3-5 days", "en", "COVID-19と3-5日", "ja"), [])

    def test_two_level_rates(self):
        self.assertTrue(ct.check_numbers("5 mg/kg/day", "en", "5 mg/kg", "ja"))
        self.assertTrue(ct.check_numbers("5 mg/kg/dose", "en", "5 mg/kg/日", "ja"))
        self.assertEqual(ct.check_numbers("5 mg/kg/day", "en", "5 mg/kg/日", "ja"), [])
        self.assertEqual(ct.check_numbers("5 mg per day", "en", "5mg/日", "ja"), [])
        self.assertEqual(ct.check_numbers("5 mg/kg/dose", "en", "5 mg/kg/回", "ja"), [])

    def test_spoons(self):
        for en in ("2 teaspoons of salt", "2 tsp of salt"):
            with self.subTest(en=en):
                self.assertTrue(ct.check_numbers(en, "en", "塩大さじ2", "ja"))
                self.assertEqual(ct.check_numbers(en, "en", "塩小さじ2", "ja"), [])
        self.assertEqual(ct.check_numbers("1 tablespoon of sugar", "en", "砂糖大さじ一", "ja"), [])
        self.assertTrue(ct.check_numbers("1 cup of water", "en", "水1杯", "ja"))

    def test_lost_frequency_and_time(self):
        self.assertTrue(ct.check_numbers("Give 5 mg daily.", "en", "5mgを与えます。", "ja"))
        self.assertEqual(ct.check_numbers("Give 5 mg daily.", "en", "毎日5mgを与えます。", "ja"), [])
        self.assertEqual(ct.check_numbers("Prepare for earthquakes in daily life.", "en", "日頃から備えます。", "ja"), [])
        self.assertTrue(ct.check_numbers("Wait 5 minutes.", "en", "5待ちます。", "ja"))
        self.assertEqual(ct.check_numbers("100 to 120 per minute", "en", "1分間に100〜120回", "ja"), [])

    def test_fahrenheit_is_not_japanese_degrees(self):
        self.assertTrue(ct.check_numbers("38 °F", "en", "体温38度", "ja"))
        self.assertEqual(ct.check_numbers("a slope of 30 degrees", "en", "30度の斜面", "ja"), [])

    def test_compound_number_words_and_formulas(self):
        self.assertEqual(ct.check_numbers("twenty-five people, a dozen eggs", "en", "25人、卵12個", "ja"), [])
        self.assertEqual(ct.check_numbers("CO₂ in 1 m²", "en", "1 m²のCO₂", "ja"), [])

    def test_minus_with_number_words(self):
        for en, ja in (("It is minus five degrees", "気温は5度"), ("five degrees below zero", "5度"),
                       ("negative five °C", "5℃"), ("5 degrees", "マイナス五度")):
            with self.subTest(en=en, ja=ja):
                self.assertTrue(ct.check_numbers(en, "en", ja, "ja"))
        self.assertEqual(ct.check_numbers("It is minus five degrees", "en", "マイナス五度", "ja"), [])

    def test_dashes_as_minus(self):
        for en in ("It is –5 °C", "It is ‑5 °C", "It is —5 °C"):
            with self.subTest(en=en):
                self.assertTrue(ct.check_numbers(en, "en", "5℃", "ja"))
                self.assertEqual(ct.check_numbers(en, "en", "‐5℃", "ja"), [])
        self.assertEqual(ct.check_numbers("3–5 days", "en", "3〜5日", "ja"), [])

    def test_frequencies(self):
        ja = "1錠を毎日服用する。"
        self.assertEqual(ct.check_numbers("Take 1 tablet daily.", "en", ja, "ja"), [])
        for en in ("Take 1 tablet weekly.", "Take 1 tablet every week.", "Take 1 tablet monthly.",
                   "Take 1 tablet every other day."):
            with self.subTest(en=en):
                self.assertTrue(ct.check_numbers(en, "en", ja, "ja"))
        self.assertEqual(ct.check_numbers("Take 1 tablet every other day.", "en", "隔日で1錠服用する。", "ja"), [])
        self.assertEqual(ct.check_numbers("3 times a day", "en", "1日3回", "ja"), [])
        self.assertEqual(ct.check_numbers("Updated every hour", "en", "1時間ごとに更新", "ja"), [])
        self.assertEqual(ct.check_numbers("every second counts; each person can help", "en",
                                          "一刻を争います。一人ひとりが助けられます。", "ja"), [])

    def test_rate_is_not_a_duration(self):
        self.assertTrue(ct.check_numbers("Give 5 mg per day.", "en", "5mgを1日間与える。", "ja"))
        self.assertTrue(ct.check_numbers("Give 5 mg for 1 day.", "en", "5mg/日を与える。", "ja"))
        self.assertEqual(ct.check_numbers("Give 5 mg for 1 day.", "en", "5mgを1日間与える。", "ja"), [])

    def test_japanese_ascii_units_and_case(self):
        self.assertEqual(ct.check_numbers("100 km/h", "en", "100km/h", "ja"), [])
        self.assertEqual(ct.check_numbers("5 mL/kg", "en", "5mL/Kg", "ja"), [])
        self.assertEqual(ct.check_numbers("for the second time", "en", "2度目", "ja"), [])

    def test_rate_with_an(self):
        self.assertTrue(ct.check_numbers("Give 100 ml an hour.", "en", "100mlを与えます。", "ja"))
        self.assertEqual(ct.check_numbers("Give 100 ml an hour.", "en", "1時間に100mlを与えます。", "ja"), [])
        self.assertEqual(ct.check_numbers("Check once an hour.", "en", "1時間に1回確認します。", "ja"), [])

    def test_ratios(self):
        self.assertTrue(ct.check_numbers("Mix bleach and water 1:10.", "en", "漂白剤と水を10:1で混ぜます。", "ja"))
        self.assertEqual(ct.check_numbers("Mix bleach and water 1:10.", "en", "漂白剤と水を1：10で混ぜます。", "ja"), [])
        self.assertEqual(ct.check_numbers("at 10:30", "en", "10時30分に", "ja"),
                         [("10", "10 in the translation, not in the original"),
                          ("10:30", "10:30 in the original, missing in the translation"),
                          ("30", "30 in the translation, not in the original")])

    def test_frequency_on_one_side_only(self):
        self.assertTrue(ct.check_numbers("Use 1 mL monthly.", "en", "1mLを1個使います。", "ja"))
        self.assertEqual(ct.check_numbers("50 km/h", "en", "50km/hで走る", "ja"), [])

    def test_compound_durations(self):
        for en, ja in (("1 hour 30 minutes", "1時間30分"), ("1 minute 30 seconds", "1分30秒"),
                       ("1 day 3 hours", "1日3時間"), ("1 week 2 days", "1週間2日")):
            with self.subTest(ja=ja):
                self.assertEqual(ct.check_numbers(en, "en", ja, "ja"), [])
        self.assertEqual(ct.check_numbers("3 tablets a day", "en", "1日3錠", "ja"), [])

    def test_concentrations(self):
        self.assertTrue(ct.check_numbers("Give 5 mg/mL.", "en", "5 mgを与える。", "ja"))
        self.assertTrue(ct.check_numbers("5 mg/mL", "en", "5 mg/L", "ja"))
        self.assertTrue(ct.check_numbers("5 mg/m²", "en", "5 mg", "ja"))
        self.assertEqual(ct.check_numbers("5 mg/mL", "en", "5 mg/mL", "ja"), [])

    def test_more_frequencies(self):
        for en in ("Take 5 mg every night.", "Take 5 mg every morning.", "Take 5 mg yearly.", "Take 5 mg each time."):
            with self.subTest(en=en):
                self.assertTrue(ct.check_numbers(en, "en", "5 mgを服用する。", "ja"))
        self.assertEqual(ct.check_numbers("Take 5 mg every night.", "en", "毎晩5 mgを服用する。", "ja"), [])

    def test_label_with_colon_is_not_a_ratio(self):
        self.assertEqual(ct.check_numbers("Step 1: wait 5 minutes.", "en", "手順1：5分待つ。", "ja"), [])
        self.assertTrue(ct.check_numbers("Wait 2:30 minutes.", "en", "2:30時間待つ。", "ja"))

    def test_japanese_area_rate(self):
        self.assertEqual(ct.check_numbers("5 mg/m²", "en", "5mg/m²", "ja"), [])
        self.assertTrue(ct.check_numbers("5 mg/m²", "en", "5mg/m", "ja"))
        self.assertTrue(ct.check_numbers("5 mg/dL", "en", "5mg", "ja"))

    def test_common_correct_japanese_passes(self):
        for en, ja in (("10 to 15 days", "10日-15日"), ("10 to 15 minutes", "10分-15分"),
                       ("3 times a week", "週3回"), ("once a month", "月に1回"), ("5 days a week", "週5日"),
                       ("a second-degree burn", "2度の熱傷"), ("give it in 2 doses", "2度に分けて与える"),
                       ("about an hour", "約1時間"), ("2 tablets at a time", "1回2錠"),
                       ("5 m per second", "毎秒5m"), ("2 capsules", "2カプセル"), ("5 ug", "5μg")):
            with self.subTest(ja=ja):
                self.assertEqual(ct.check_numbers(en, "en", ja, "ja"), [])
        self.assertTrue(ct.check_numbers("3 times a week", "en", "週3日", "ja"))
        self.assertTrue(ct.check_numbers("38 °C", "en", "38度以上の熱", "ja") == [])
        self.assertTrue(ct.check_numbers("38 °F", "en", "38度以上の熱", "ja"))
        self.assertTrue(ct.check_numbers("-15 °C", "en", "外は15℃", "ja"))

    def test_phone_number(self):
        self.assertEqual(ct.check_numbers("119番に電話します", "ja", "Call 110.", "en"),
                         [("110", "110 in the translation, not in the original"),
                          ("119", "119 in the original, missing in the translation")])


class Words(unittest.TestCase):
    def items(self, src, dst, a="en", b="ja"):
        return [g for g, _ in ct.check_words(src, a, dst, b, TERMS)]

    def test_lost_prohibition(self):
        self.assertEqual(self.items("Do not rub frostbitten skin.", "凍傷の皮膚をこすります。"), ["prohibition"])

    def test_lost_urgency_japanese_to_english(self):
        self.assertEqual(self.items("すぐに高台へ避難してください。", "Evacuate to high ground.", "ja", "en"),
                         ["immediately"])

    def test_counted_per_sentence(self):
        src = "Do not rub the skin. Do not walk on frozen feet."
        self.assertEqual(self.items(src, "皮膚をこすってはいけません。凍った足で歩きます。"), ["prohibition"])
        self.assertEqual(self.items(src, "皮膚をこすってはいけません。凍った足で歩いてはいけません。"), [])

    def test_split_sentence_is_not_a_loss(self):
        src = "Crack the windows, and do not allow anyone to sleep in a running vehicle."
        self.assertEqual(self.items(src, "窓を少し開けます。また、エンジンがかかっている車の中で誰も寝かせてはいけません。"), [])

    def test_common_words_are_not_matches(self):
        # 非常に is "very", not 常に "always"; できるだけ is not だけ "only".
        self.assertEqual(self.items("This is very dangerous; move as far as possible.",
                                    "非常に危険です。できるだけ遠くへ移動します。"), [])


class Exceptions(unittest.TestCase):
    SRC, DST = "Rest for 2 hours.", "休みます。"

    def accept(self, reason="said in the next sentence", text=DST):
        return [{"check": "numbers", "item": "2", "for": ct.passage_key(text), "reason": reason}]

    def test_accepted_with_reason(self):
        found, unused = flags(self.SRC, self.DST, accept=self.accept())
        self.assertEqual([lvl for _, _, _, lvl in found], ["accepted"])
        self.assertEqual(unused, [])

    def test_exception_without_reason_does_not_count(self):
        found, _ = flags(self.SRC, self.DST, accept=self.accept(" "))
        self.assertEqual([lvl for _, _, _, lvl in found], ["error"])

    def test_edited_translation_brings_the_flag_back(self):
        found, unused = flags(self.SRC, "少し休みます。", accept=self.accept())
        self.assertEqual([lvl for _, _, _, lvl in found], ["error"])
        self.assertIn(ct.passage_key("少し休みます。"), found[0][2])  # tells what to set
        self.assertEqual(len(unused), 1)
        self.assertIn("another translation text", unused[0])

    def test_unused_exception_is_listed(self):
        _, unused = flags(self.SRC, "2時間休みます。", accept=self.accept(text="2時間休みます。"))
        self.assertEqual(unused, ["numbers 2: no such flag any more; remove it"])

    def test_heading_exception(self):
        accept = [{"check": "numbers", "item": "1", "for": ct.passage_key("Hourly forecast"), "reason": "1時間ごと"}]
        found, _ = ct.check_heading("1時間 ごとの予報", "Hourly forecast", accept, "ja", "en")
        self.assertEqual([lvl for _, _, _, lvl in found], [])  # hourly = 1 hour: no flag
        found, unused = ct.check_heading("2時間ごとの予報", "Forecast", accept, "ja", "en")
        self.assertEqual([lvl for _, _, _, lvl in found], ["error"])
        self.assertEqual(len(unused), 1)


class Integrity(unittest.TestCase):
    def test_key_must_be_hash_of_source(self):
        found, _ = ct.check_entry("0000000000000000", {"source": "Rest.", "text": "休みます。"}, "en", "ja", TERMS)
        self.assertEqual([(c, i) for c, i, _, _ in found], [("integrity", "key")])

    def test_source_is_required(self):
        found, _ = ct.check_entry("0000000000000000", {"text": "休みます。"}, "en", "ja", TERMS)
        self.assertEqual([(c, lvl) for c, _, _, lvl in found], [("integrity", "error")])


class BadEntries(unittest.TestCase):
    def test_bad_shapes_are_integrity_errors(self):
        for e in ({"source": "Rest.", "text": None}, {"source": "Rest.", "text": "休む", "accept": "x"},
                  {"source": "Rest.", "text": "休む", "accept": ["x"]}, "text", {"source": "Rest.", "text": ["x"]},
                  {"source": "Rest.", "text": "", "accept": [{"check": ["numbers"], "item": "1"}]}):
            with self.subTest(e=e):
                found, _ = ct.check_entry(ct.passage_key("Rest."), e, "en", "ja", TERMS)
                self.assertIn("error", [lvl for _, _, _, lvl in found])


    def test_bad_heading_is_an_error(self):
        found, _ = ct.check_heading("見出し", None, [], "ja", "en")
        self.assertEqual([lvl for _, _, _, lvl in found], ["error"])


class FileAndReport(unittest.TestCase):
    def test_report_lists_flagged_passages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intl.firstaid.basics.ja.json"
            passages = dict([entry(SNAKEBITE, SNAKEBITE_BAD), entry("Rest for 2 hours.", "休みます。"),
                             entry("Rest.", "休みます。")])
            path.write_text(json.dumps({"headings": {"STEP 2": "手順3"}, "passages": passages},
                                       ensure_ascii=False), encoding="utf-8")
            errors, warnings = ct.check_file(path, Path(tmp) / "out", quiet=True)
            report = (Path(tmp) / "out" / "intl.firstaid.basics.ja.md").read_text(encoding="utf-8")
        self.assertEqual(errors, 3)  # 2 hours lost; heading 2 → 3 (two flags)
        self.assertEqual(warnings, 3)
        self.assertIn("snug but not too tight", report)
        self.assertIn("しっかりと縛って", report)
        self.assertIn("not reviewed", report)
        self.assertNotIn(ct.passage_key("Rest."), report)


class Updates(unittest.TestCase):
    def test_stale_entry_that_is_not_an_object(self):
        ct.print_changes({"headings": {}, "passages": {"k": "text"}}, [], "test")  # no crash

    def test_new_and_stale_passages(self):
        old_key, old = entry("Rest for 2 hours.", "2時間休みます。")
        tr = {"headings": {}, "passages": {old_key: old}}
        new, stale = ct.changes(tr, [("Rest", "Rest for 3 hours.")])
        self.assertEqual(new, [("Rest", "Rest for 3 hours.")])
        self.assertEqual([k for k, _ in stale], [old_key])


class RepositoryFiles(unittest.TestCase):
    def test_word_list_patterns_compile_for_both_languages(self):
        data = json.loads((ct.TRANSLATIONS / "terms.en-ja.json").read_text(encoding="utf-8"))
        ids = [g["id"] for g in data["groups"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(TERMS), set(ids))


if __name__ == "__main__":
    unittest.main()
