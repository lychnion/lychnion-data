"""Lychnion's status in the catalog (lychnion/lychnion#117), through
tool/records.dart with a throwaway key: signed with every catalog, naming
the newest version of each item and the withdrawn attestations, carried by
export, checked by verify. The key never shows in the output.

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


def model(version):
    return {
        "id": "model.test",
        "kind": "model",
        "role": "chat",
        "version": version,
        "title": {"ja": "テスト", "en": "Test"},
        "licence": "test",
        "files": [
            {
                "name": "test.gguf",
                "size": 1,
                "sha256": "a" * 64,
                "url": "https://example.org/test.gguf",
            }
        ],
    }


@unittest.skipUnless(shutil.which("dart"), "needs Dart")
class StatusTest(unittest.TestCase):
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
        cls.otherSeed, _ = keygen("other")
        with open(cls.seed) as f:
            cls.seedText = f.read().strip()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def setUp(self):
        self.work = tempfile.mkdtemp(dir=self.dir)
        self.records = os.path.join(self.work, "records")
        self.att = os.path.join(self.work, "attestations")
        os.makedirs(self.att)
        self.items = os.path.join(self.work, "items.json")
        self.out = os.path.join(self.work, "catalog.json")

    def write_items(self, version):
        with open(self.items, "w") as f:
            json.dump({"publisher": "lychnion", "items": [model(version)]}, f)

    def sign(self, version):
        self.write_items(version)
        dart("sign", "--key", self.seed, "--public-key", self.pub,
             "--items", self.items, "--records", self.records, "model.test")

    def catalog(self, *extra, ok=True, key=True, stdin=None):
        k = (["--key", "-" if stdin is not None else self.seed] if key else [])
        return dart(
            "catalog", *k, "--public-key", self.pub,
            "--items", self.items, "--records", self.records,
            "--base", "https://example.org/", "--out", self.out,
            "--attestations", self.att, *extra, ok=ok, stdin=stdin,
        )

    def doc(self):
        with open(self.out) as f:
            return json.load(f)

    def test_signed_with_the_catalog_and_verified(self):
        self.sign("2026.11.1")
        # The key from stdin, as from `op read`.
        r = self.catalog(stdin=self.seedText + "\n")
        self.assertIn("status signed", r.stdout)
        st = self.doc()["status"]
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["publisher"], "lychnion")
        self.assertEqual(st[0]["items"], {"model.test": "2026.11.1"})
        out = dart("verify", "--public-key", self.pub, self.out).stdout
        self.assertIn("ok   status lychnion", out)

    def test_withdrawals_go_into_the_status(self):
        self.sign("2026.11.1")
        city = dart("keygen").stderr.strip().removeprefix("public key: ")
        app = os.path.join(self.work, "app.json")
        with open(app, "w") as f:
            json.dump({"publisher": "jp.city.x", "key": city,
                       "title": {"ja": "例市", "en": "Example City"},
                       "url": "https://www.city.example.lg.jp/"}, f)
        dart("attest", "--key", self.seed, "--public-key", self.pub,
             "--attestations", self.att, "--from", app)
        time.sleep(1)  # never the same signed time
        dart("attest", "--key", self.seed, "--public-key", self.pub,
             "--attestations", self.att, "--withdraw", "--publisher", "jp.city.x")
        self.catalog()
        st = self.doc()["status"][0]
        self.assertEqual(len(st["attestations"]), 1)
        self.assertTrue(st["attestations"][0]["withdrawn"])

    def test_verify_catches_a_status_older_than_its_records(self):
        self.sign("2026.11.1")
        self.catalog()
        old = self.doc()
        self.sign("2026.11.2")
        self.catalog("--no-status", key=False)
        doc = self.doc()
        # The new record with the old status: not signed again.
        doc["status"] = old["status"]
        with open(self.out, "w") as f:
            json.dump(doc, f)
        # Before a release: an error. Elsewhere (a stick, a node): a note.
        r = dart("verify", "--public-key", self.pub, self.out, "--release",
                 ok=False)
        self.assertIn("BAD  model.test 2026.11.2: the status names 2026.11.1",
                      r.stdout)
        r = dart("verify", "--public-key", self.pub, self.out)
        self.assertIn("the status names 2026.11.1", r.stdout)
        # The other way round (a newer version held back here) is fine.
        self.catalog()
        newer = self.doc()["status"]
        old["status"] = newer
        with open(self.out, "w") as f:
            json.dump(old, f)
        r = dart("verify", "--public-key", self.pub, self.out)
        self.assertIn("version 2026.11.2 exists elsewhere", r.stdout)

    def test_release_needs_a_status(self):
        self.sign("2026.11.1")
        r = self.catalog("--no-status", key=False)
        self.assertIn("NO STATUS: not for release", r.stdout)
        r = dart("verify", "--public-key", self.pub, self.out, "--release",
                 ok=False)
        self.assertIn("no status of Lychnion's: not for release", r.stdout)
        r = dart("verify", "--public-key", self.pub, self.out)
        self.assertIn("no status of Lychnion's", r.stdout)
        self.catalog()
        dart("verify", "--public-key", self.pub, self.out, "--release")

    def test_lychnions_own_key_never_skips_the_checks(self):
        self.sign("2026.11.1")
        with open(os.path.join(ROOT, "keys", "lychnion.pub")) as f:
            builtin = f.read().strip()
        # Also spelled with padding: the same key.
        for key in [builtin, builtin + "="]:
            r = dart("catalog", "--public-key", key, "--items", self.items,
                     "--records", self.records, "--base", "https://example.org/",
                     "--out", self.out, "--no-attestations", ok=False)
            self.assertIn("--no-status", r.stderr, key)

    def test_release_catalog_without_records_needs_a_status_too(self):
        with open(self.items, "w") as f:
            json.dump({"publisher": "lychnion", "items": []}, f)
        os.makedirs(self.records, exist_ok=True)
        self.catalog("--no-status", key=False)
        r = dart("verify", "--public-key", self.pub, self.out, "--release",
                 ok=False)
        self.assertIn("no status of Lychnion's: not for release", r.stdout)

    def test_reattested_publisher_has_no_withdrawal(self):
        self.sign("2026.11.1")
        city = dart("keygen").stderr.strip().removeprefix("public key: ")
        app = os.path.join(self.work, "app.json")
        with open(app, "w") as f:
            json.dump({"publisher": "jp.city.x", "key": city,
                       "title": {"ja": "例市", "en": "Example City"},
                       "url": "https://www.city.example.lg.jp/"}, f)
        base = ["--key", self.seed, "--public-key", self.pub,
                "--attestations", self.att]
        dart("attest", *base, "--from", app)
        time.sleep(1)
        dart("attest", *base, "--withdraw", "--publisher", "jp.city.x")
        time.sleep(1)
        dart("attest", *base, "--from", app)
        self.catalog()
        self.assertNotIn("attestations", self.doc()["status"][0])

    def test_items_file_newer_than_the_records(self):
        self.sign("2026.11.1")
        self.write_items("2026.11.2")
        r = self.catalog(ok=False)
        self.assertIn("sign it first", r.stderr)

    def test_export_passes_it_on(self):
        self.sign("2026.11.1")
        self.catalog()
        node = os.path.join(self.work, "node")
        dart("export", "--public-key", self.pub, self.out, "--out", node)
        with open(os.path.join(node, "catalog.json")) as f:
            self.assertEqual(len(json.load(f)["status"]), 1)

    def test_refused(self):
        self.sign("2026.11.1")
        # Lychnion's catalog without a key or --no-status.
        r = dart("catalog", "--items", self.items, "--records", self.records,
                 "--base", "https://example.org/", "--out", self.out,
                 "--no-attestations", ok=False)
        self.assertIn("--no-status", r.stderr)
        # Both.
        r = self.catalog("--no-status", ok=False)
        self.assertIn("give one", r.stderr)
        # A key not trusted for Lychnion.
        r = dart(
            "catalog", "--key", self.otherSeed, "--public-key", self.pub,
            "--items", self.items, "--records", self.records,
            "--base", "https://example.org/", "--out", self.out,
            "--attestations", self.att, ok=False,
        )
        self.assertIn("not trusted for Lychnion", r.stderr)
        # A broken key is never shown.
        bad = self.seedText[:-2] + "!!"
        r = self.catalog(stdin=bad + "\n", ok=False)
        self.assertNotIn(bad, r.stdout + r.stderr)
        # Nor a seed given as a stray argument, as an option value, or as
        # the --key file name.
        for extra in [[self.seedText], ["--key=" + self.seedText]]:
            r = self.catalog(*extra, ok=False)
            self.assertNotIn(self.seedText, r.stdout + r.stderr)
        r = dart("catalog", "--key", self.seedText, "--public-key", self.pub,
                 "--items", self.items, "--records", self.records,
                 "--base", "https://example.org/", "--out", self.out,
                 "--attestations", self.att, ok=False)
        self.assertNotIn(self.seedText, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
