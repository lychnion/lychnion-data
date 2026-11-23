import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_vectors  # noqa: E402


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
        self.a = os.path.join(self.dir, "a.sqlite")
        self.b = os.path.join(self.dir, "b.sqlite")
        package(self.a, "a", "1", 2)
        package(self.b, "b", "7", 3)

    def cache(self, name, n):
        path = os.path.join(self.dir, name)
        json.dump([[float(i)] for i in range(n)], open(path, "w"))
        return path

    def test_splits_in_order(self):
        c = self.cache("a+b-1+7-m.gguf@en.vectors.json", 5)
        out = os.path.join(self.dir, "out")
        split_vectors.split(c, out, [self.b, self.a])
        self.assertEqual(json.load(open(os.path.join(out, "a.vectors.json"))), [[0.0], [1.0]])
        self.assertEqual(json.load(open(os.path.join(out, "b.vectors.json"))), [[2.0], [3.0], [4.0]])

    def test_refuses_another_version(self):
        c = self.cache("a+b-1+8-m.gguf.vectors.json", 5)
        with self.assertRaises(SystemExit):
            split_vectors.split(c, os.path.join(self.dir, "out"), [self.a, self.b])

    def test_refuses_another_count(self):
        c = self.cache("a+b-1+7-m.gguf.vectors.json", 6)
        with self.assertRaises(SystemExit):
            split_vectors.split(c, os.path.join(self.dir, "out"), [self.a, self.b])

    def test_needs_every_package(self):
        c = self.cache("a+b-1+7-m.gguf.vectors.json", 5)
        with self.assertRaises(SystemExit):
            split_vectors.split(c, os.path.join(self.dir, "out"), [self.a])


if __name__ == "__main__":
    unittest.main()
