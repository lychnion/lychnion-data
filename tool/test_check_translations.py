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

    def test_accepted_with_reason(self):
        found, unused = flags(self.SRC, self.DST,
                              accept=[{"check": "numbers", "item": "2", "reason": "said in the next sentence"}])
        self.assertEqual([lvl for _, _, _, lvl in found], ["accepted"])
        self.assertEqual(unused, [])

    def test_exception_without_reason_does_not_count(self):
        found, _ = flags(self.SRC, self.DST, accept=[{"check": "numbers", "item": "2", "reason": " "}])
        self.assertEqual([lvl for _, _, _, lvl in found], ["error"])

    def test_unused_exception_is_listed(self):
        _, unused = flags(self.SRC, "2時間休みます。", accept=[{"check": "numbers", "item": "2", "reason": "x"}])
        self.assertEqual(unused, ["numbers 2"])


class Integrity(unittest.TestCase):
    def test_key_must_be_hash_of_source(self):
        found, _ = ct.check_entry("0000000000000000", {"source": "Rest.", "text": "休みます。"}, "en", "ja", TERMS)
        self.assertEqual([(c, i) for c, i, _, _ in found], [("integrity", "key")])

    def test_source_is_required(self):
        found, _ = ct.check_entry("0000000000000000", {"text": "休みます。"}, "en", "ja", TERMS)
        self.assertEqual([(c, lvl) for c, _, _, lvl in found], [("integrity", "error")])


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
