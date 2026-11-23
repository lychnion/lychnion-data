"""Attestations through tool/records.dart (lychnion/lychnion#119): signed
with a test key, checked, taken back, carried by catalog, export and
verify, and refused when changed or signed by the wrong key.

Runs `dart run tool/records.dart`, so it needs Dart and `dart pub get`.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dart(*args, ok=True):
    r = subprocess.run(
        ["dart", "run", "tool/records.dart", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if ok and r.returncode != 0:
        raise AssertionError(f"{args[0]} failed: {r.stdout}{r.stderr}")
    return r


@unittest.skipUnless(shutil.which("dart"), "needs Dart")
class AttestationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()

        def keygen(name):
            r = dart("keygen")
            path = os.path.join(cls.dir, name)
            with open(path, "w") as f:
                f.write(r.stdout)
            return path, r.stderr.strip().removeprefix("public key: ")

        cls.seed, cls.pub = keygen("lychnion")
        cls.citySeed, cls.city = keygen("city")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def setUp(self):
        self.att = tempfile.mkdtemp(dir=self.dir)

    def attest(self, *extra, ok=True, key=None):
        return dart(
            "attest",
            "--key", key or self.seed,
            "--public-key", self.pub,
            "--attestations", self.att,
            "--publisher", "jp.city.sendai",
            *extra,
            ok=ok,
        )

    def sendai(self):
        return self.attest(
            "--publisher-key", self.city,
            "--title-ja", "仙台市",
            "--title-en", "Sendai City",
            "--url", "https://www.city.sendai.jp/",
        )

    def check(self, ok=True):
        return dart("check-attestations", "--public-key", self.pub, self.att, ok=ok)

    def test_sign_check_withdraw_and_carry(self):
        path = self.sendai().stdout.strip()
        self.assertTrue(path.startswith(os.path.join(self.att, "jp.city.sendai")))
        with open(path) as f:
            a = json.load(f)
        self.assertEqual(a["publisher"], "jp.city.sendai")
        self.assertEqual(a["title"], {"ja": "仙台市", "en": "Sendai City"})
        self.assertEqual(a["method"], "official-website")
        self.assertIn("1 attestations", self.check().stdout)

        # Taken back: names the newest attestation's key by itself.
        # (A second later, so the two never share a signed time.)
        subprocess.run(["sleep", "1"])
        self.attest("--withdraw")
        out = self.check().stdout
        self.assertIn("withdrawn", out)
        self.assertIn("replaced by a newer one", out)

        items = os.path.join(self.dir, "items.json")
        with open(items, "w") as f:
            json.dump({"items": []}, f)
        records = tempfile.mkdtemp(dir=self.dir)
        catalog = os.path.join(self.att, "..", os.path.basename(self.att) + ".json")
        dart(
            "catalog", "--public-key", self.pub, "--items", items,
            "--records", records, "--base", "https://example.org/",
            "--out", catalog, "--attestations", self.att,
        )
        with open(catalog) as f:
            self.assertEqual(len(json.load(f)["attestations"]), 2)
        self.assertIn(
            "2 attestations", dart("verify", "--public-key", self.pub, catalog).stdout
        )
        node = tempfile.mkdtemp(dir=self.dir)
        dart("export", "--public-key", self.pub, catalog, "--out", node)
        with open(os.path.join(node, "catalog.json")) as f:
            self.assertEqual(len(json.load(f)["attestations"]), 2)

    def test_changed_or_misplaced_files_are_refused(self):
        path = self.sendai().stdout.strip()
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("仙台市", "偽仙台市"))
        self.assertNotEqual(self.check(ok=False).returncode, 0)
        # Valid, but under another publisher's folder.
        with open(path, "w") as f:
            f.write(text)
        other = os.path.join(self.att, "jp.city.other")
        os.makedirs(other)
        shutil.move(path, os.path.join(other, os.path.basename(path)))
        r = self.check(ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("should be", r.stderr)

    def test_refused_when_not_signed_by_lychnion_or_incomplete(self):
        r = self.attest(
            "--publisher-key", self.city, "--title-ja", "市", "--title-en",
            "City", "--url", "https://x.lg.jp/", key=self.citySeed, ok=False,
        )
        self.assertIn("not trusted for Lychnion", r.stderr)
        r = self.attest(
            "--publisher-key", self.city, "--title-ja", "市", "--title-en",
            "City", ok=False,
        )
        self.assertIn("--url", r.stderr)
        r = self.attest(
            "--publisher-key", self.city, "--title-ja", "市", "--title-en",
            "City", "--url", "http://x.lg.jp/", ok=False,
        )
        self.assertIn("the app would accept", r.stderr)
        self.assertEqual(os.listdir(self.att), [])
        r = self.attest("--withdraw", ok=False)
        self.assertIn("no attestation to take back", r.stderr)


if __name__ == "__main__":
    unittest.main()
