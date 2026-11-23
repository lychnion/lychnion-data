import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_vectors  # noqa: E402

MODEL = "embeddinggemma-2-Q8_0.gguf@en"  # a model name with '-' in it


def package(path, pid, version, n):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE passages(id INTEGER PRIMARY KEY, text TEXT)")
    db.executemany("INSERT INTO meta VALUES(?,?)", [("id", pid), ("version", version)])
    db.executemany("INSERT INTO passages(text) VALUES(?)", [(f"p{i}",) for i in range(n)])
    db.commit()
    db.close()


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.out = os.path.join(self.dir, "out")
        self.a = os.path.join(self.dir, "a.sqlite")
        self.b = os.path.join(self.dir, "b.sqlite")
        package(self.a, "jp.a", "2026.11.4", 2)
        package(self.b, "intl.b", "2026.11.1", 3)

    def cache(self, name, n):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            json.dump([[float(i)] for i in range(n)], f)
        return path

    def run_split(self, cache, packages):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            split_vectors.split(cache, self.out, packages)
        return out.getvalue()

    def refused(self, cache, packages, words):
        with self.assertRaises(SystemExit) as e:
            self.run_split(cache, packages)
        self.assertIn(words, str(e.exception))
        self.assertFalse(os.path.exists(self.out), "nothing is written when a check fails")

    def test_splits_in_the_cache_order(self):
        c = self.cache(f"jp.a+intl.b-2026.11.4+2026.11.1-{MODEL}.vectors.json", 5)
        printed = self.run_split(c, [self.b, self.a])
        a = os.path.join(self.out, f"jp.a.{MODEL}.vectors.json")
        b = os.path.join(self.out, f"intl.b.{MODEL}.vectors.json")
        self.assertEqual(json.load(open(a)), [[0.0], [1.0]])
        self.assertEqual(json.load(open(b)), [[2.0], [3.0], [4.0]])
        # The command to add them names the model from the cache.
        self.assertIn(f"tool/add_vectors.py {self.a} {a} {MODEL}", printed)

    def test_refuses_another_version(self):
        c = self.cache(f"jp.a+intl.b-2026.11.4+2026.11.2-{MODEL}.vectors.json", 5)
        self.refused(c, [self.a, self.b], "version 2026.11.2")

    def test_refuses_another_count(self):
        c = self.cache(f"jp.a+intl.b-2026.11.4+2026.11.1-{MODEL}.vectors.json", 6)
        self.refused(c, [self.a, self.b], "6 vectors for 5 passages")

    def test_needs_every_package(self):
        c = self.cache(f"jp.a+intl.b-2026.11.4+2026.11.1-{MODEL}.vectors.json", 5)
        self.refused(c, [self.a], "also covers intl.b")

    def test_refuses_a_name_it_cannot_read(self):
        self.refused(self.cache("jp.a-2026.11.4+2026.11.1-m.vectors.json", 5), [self.a], "1 ids but 2")
        self.refused(self.cache("jp.a-2026.11.4.vectors.json", 2), [self.a], "not <ids>")
        self.refused(self.cache("jp.a-2026.11.4-m.json", 2), [self.a], "not a .vectors.json")

    def test_refuses_an_id_with_a_dash(self):
        c = os.path.join(self.dir, "c.sqlite")
        package(c, "jp.c-x", "1", 1)
        self.refused(self.cache("jp.c-x-1-m.vectors.json", 1), [c], "has '-' or '+'")


if __name__ == "__main__":
    unittest.main()
