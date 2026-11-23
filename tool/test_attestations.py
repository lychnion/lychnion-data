"""Attestations through tool/records.dart (lychnion/lychnion#119): signed
with a test key, checked, taken back, carried by catalog, export and
verify, and refused when changed, misplaced, signed by the wrong key or
not newer. The private key never shows in the output.

Runs `dart run tool/records.dart`, so it needs Dart and `dart pub get`.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dart(*args, ok=True, stdin=None):
    r = subprocess.run(
        ["dart", "run", "tool/records.dart", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        input=stdin,
    )
    if ok and r.returncode != 0:
        raise AssertionError(f"{args[0]} failed: {r.stdout}{r.stderr}")
    if not ok and r.returncode == 0:
        raise AssertionError(f"{args[0]} should fail: {r.stdout}{r.stderr}")
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
        with open(cls.seed) as f:
            cls.seedText = f.read().strip()
        cls.items = os.path.join(cls.dir, "items.json")
        with open(cls.items, "w") as f:
            json.dump({"items": []}, f)
        cls.records = tempfile.mkdtemp(dir=cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def setUp(self):
        self.att = tempfile.mkdtemp(dir=self.dir)

    def application(self, **change):
        a = {
            "publisher": "jp.city.sendai",
            "key": self.city,
            "title": {"ja": "仙台市", "en": "Sendai City"},
            "url": "https://www.city.sendai.jp/",
        }
        a.update(change)
        path = os.path.join(self.dir, "application.json")
        with open(path, "w") as f:
            json.dump(a, f)
        return path

    def attest(self, *extra, ok=True, key=None, stdin=None):
        return dart(
            "attest",
            "--key", "-" if stdin is not None else (key or self.seed),
            "--public-key", self.pub,
            "--attestations", self.att,
            *extra,
            ok=ok,
            stdin=stdin,
        )

    def sendai(self, **change):
        return self.attest("--from", self.application(**change))

    def check(self, ok=True):
        return dart("check-attestations", "--public-key", self.pub, self.att, ok=ok)

    def catalog(self, *extra, ok=True):
        out = os.path.join(self.dir, "catalog.json")
        r = dart(
            "catalog", "--public-key", self.pub, "--items", self.items,
            "--records", self.records, "--base", "https://example.org/",
            "--out", out, *extra, ok=ok,
        )
        return r, out

    def test_sign_check_withdraw_and_carry(self):
        # The documented path: the key on stdin, as from `op read`.
        path = self.attest(
            "--from", self.application(), stdin=self.seedText + "\n"
        ).stdout.strip()
        self.assertTrue(path.startswith(os.path.join(self.att, "jp.city.sendai")))
        with open(path) as f:
            a = json.load(f)
        self.assertEqual(a["publisher"], "jp.city.sendai")
        self.assertEqual(a["title"], {"ja": "仙台市", "en": "Sendai City"})
        self.assertEqual(a["method"], "official-website")
        self.assertIn("1 attestations", self.check().stdout)

        time.sleep(1)  # never the same signed time
        out = self.attest("--withdraw", "--publisher", "jp.city.sendai").stdout
        self.assertIn(self.city, out)
        out = self.check().stdout
        self.assertIn("withdrawn (decides)", out)
        self.assertIn("replaced by a newer one", out)
        # A second withdrawal is refused.
        r = self.attest("--withdraw", "--publisher", "jp.city.sendai", ok=False)
        self.assertIn("already withdrawn", r.stderr)

        r, catalog = self.catalog("--attestations", self.att)
        with open(catalog) as f:
            self.assertEqual(len(json.load(f)["attestations"]), 2)
        self.assertIn(
            "2 attestations", dart("verify", "--public-key", self.pub, catalog).stdout
        )
        node = tempfile.mkdtemp(dir=self.dir)
        dart("export", "--public-key", self.pub, catalog, "--out", node)
        with open(os.path.join(node, "catalog.json")) as f:
            self.assertEqual(len(json.load(f)["attestations"]), 2)

        # verify fails on a changed attestation in the catalog.
        with open(catalog) as f:
            c = json.load(f)
        c["attestations"][0]["title"]["ja"] = "偽仙台市"
        with open(catalog, "w") as f:
            json.dump(c, f)
        dart("verify", "--public-key", self.pub, catalog, ok=False)

    def test_catalog_fails_closed(self):
        path = self.sendai().stdout.strip()
        folder = os.path.dirname(path)
        # Missing folder (another working directory, a typo).
        r, _ = self.catalog("--attestations", os.path.join(self.att, "nope"), ok=False)
        self.assertIn("no such folder", r.stderr)
        # Anything that is not <publisher>/<signed>.json stops it.
        for bad in [
            os.path.join(folder, os.path.basename(path) + ".bak"),
            os.path.join(folder, "x.JSON"),
            os.path.join(self.att, "loose.json"),
            os.path.join(folder, "deeper", "y.json"),
        ]:
            os.makedirs(os.path.dirname(bad), exist_ok=True)
            shutil.copy(path, bad)
            r, _ = self.catalog("--attestations", self.att, ok=False)
            self.assertIn("BAD", r.stderr, bad)
            self.check(ok=False)
            os.remove(bad)
            if bad.endswith(os.path.join("deeper", "y.json")):
                os.rmdir(os.path.dirname(bad))
        # A link, even to a valid file.
        link = os.path.join(folder, "20990101T000000+0900.json")
        os.symlink(path, link)
        r, _ = self.catalog("--attestations", self.att, ok=False)
        self.assertIn("a link", r.stderr)
        os.remove(link)
        # A README is fine; then all is well again.
        with open(os.path.join(self.att, "README.md"), "w") as f:
            f.write("x")
        self.catalog("--attestations", self.att)
        # Finder's .DS_Store is skipped, not taken for a renamed file.
        for where in [self.att, folder]:
            with open(os.path.join(where, ".DS_Store"), "w") as f:
                f.write("x")
        r, _ = self.catalog("--attestations", self.att)
        self.assertIn("skipped", r.stderr)
        # Explicitly none; both at once is refused.
        r, _ = self.catalog("--no-attestations")
        self.assertIn("0 attestations", r.stdout)
        r, _ = self.catalog("--no-attestations", "--attestations", self.att, ok=False)
        # Another publisher's catalog (--public-key) carries none by
        # default, not Lychnion's folder next to the tool.
        r, _ = self.catalog()
        self.assertIn("0 attestations (none given)", r.stdout)

    def test_changed_or_misplaced_files_are_refused(self):
        path = self.sendai().stdout.strip()
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("仙台市", "偽仙台市"))
        self.check(ok=False)
        # Valid, but under another publisher's folder.
        with open(path, "w") as f:
            f.write(text)
        other = os.path.join(self.att, "jp.city.other")
        os.makedirs(other)
        shutil.move(path, os.path.join(other, os.path.basename(path)))
        r = self.check(ok=False)
        self.assertIn("should be", r.stderr)

    def test_refused_inputs(self):
        r = self.attest("--from", self.application(), key=self.citySeed, ok=False)
        self.assertIn("not trusted for Lychnion", r.stderr)
        r = self.attest("--from", self.application(url=None), ok=False)
        self.assertIn("url", r.stderr)
        r = self.attest("--from", self.application(url="http://x.lg.jp/"), ok=False)
        self.assertIn("the app would accept", r.stderr)
        r = self.attest("--from", self.application(), "--method", "phone", ok=False)
        self.assertIn("not written down", r.stderr)
        r = self.attest("--from", self.application(), "--checked", "2999-01-01",
                        ok=False)
        self.assertIn("future", r.stderr)
        r = self.attest("--from", self.application(extra="x"), ok=False)
        self.assertIn("only publisher, key, title and url", r.stderr)
        r = self.attest("--withdraw", "--publisher", "jp.city.sendai", ok=False)
        self.assertIn("no attestation to take back", r.stderr)
        r = self.attest("--withdraw", "--from", self.application(), ok=False)
        self.assertIn("not --from", r.stderr)
        r = self.attest("--from", self.application(), "--publisher", "jp.x",
                        ok=False)
        self.assertIn("different publishers", r.stderr)
        r = self.attest("--from", self.application(publisher=5), ok=False)
        self.assertIn('"publisher" must be text', r.stderr)
        r = self.attest("--from", self.application(), "--checked", "20261123",
                        ok=False)
        self.assertIn("YYYY-MM-DD", r.stderr)
        self.assertEqual(os.listdir(self.att), [])

    def test_titles_and_key_are_trimmed(self):
        path = self.attest(
            "--from",
            self.application(key=" " + self.city + "\n",
                             title={"ja": " 仙台市 ", "en": "Sendai City "}),
        ).stdout.strip()
        with open(path) as f:
            a = json.load(f)
        self.assertEqual(a["title"], {"ja": "仙台市", "en": "Sendai City"})

    def test_a_bad_key_is_never_shown(self):
        bad = self.seedText[:-2] + "!!"
        r = self.attest("--from", self.application(), stdin=bad + "\n", ok=False)
        self.assertNotIn(bad, r.stdout + r.stderr)
        self.assertNotIn(self.seedText[:20], r.stdout + r.stderr)
        self.assertIn("not shown", r.stderr)
        # The seed given as a file name by mistake.
        r = self.attest("--from", self.application(), key=self.seedText, ok=False)
        self.assertNotIn(self.seedText, r.stdout + r.stderr)
        # Nor in `public`, nor as a stray argument or an option value.
        r = dart("public", "--key", "-", stdin=bad + "\n", ok=False)
        self.assertNotIn(bad, r.stdout + r.stderr)
        r = dart("public", "--key", "-", self.seedText, stdin="x", ok=False)
        self.assertNotIn(self.seedText, r.stdout + r.stderr)
        r = dart("public", "--key", "-", "--key=" + self.seedText, stdin="x",
                 ok=False)
        self.assertNotIn(self.seedText, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
