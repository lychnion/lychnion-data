"""Tests for tool/contours.py.

    python3 -I -m unittest discover -s tool -p 'test_*.py'
"""
import gzip
import importlib.util
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("contours", Path(__file__).with_name("contours.py"))
contours = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contours)
V = contours.encode_varint


def field(num, payload):
    return V(num << 3 | 2) + V(len(payload)) + payload


def layer(name, extra=b""):
    # name (1), a feature (2) with an id varint, extent (5) as a varint
    feature = V(1 << 3 | 0) + V(7)
    return field(1, name.encode()) + field(2, feature) + V(5 << 3 | 0) + V(4096) + extra


def tile(*names):
    return b"".join(field(3, layer(n)) for n in names)


class Varint(unittest.TestCase):
    def test_round_trip(self):
        for n in (0, 1, 127, 128, 300, 2 ** 31, 2 ** 63):
            self.assertEqual(contours.varint(V(n), 0), (n, len(V(n))))

    def test_truncated(self):
        with self.assertRaises(ValueError):
            contours.varint(b"\x80", 0)


class KeepLayers(unittest.TestCase):
    def test_keeps_named_layers_byte_for_byte(self):
        t = tile("RdCL", "Cntr", "Anno", "Cstline")
        self.assertEqual(contours.keep_layers(t, {"Cntr", "Cstline"}), tile("Cntr", "Cstline"))

    def test_nothing_kept(self):
        self.assertEqual(contours.keep_layers(tile("RdCL", "BldA"), {"Cntr"}), b"")

    def test_utf8_names_and_other_fields(self):
        t = V(9 << 3 | 0) + V(1) + tile("等高線", "Cntr")  # an unknown varint field
        self.assertEqual(contours.keep_layers(t, {"等高線"}), tile("等高線"))

    def test_broken_tile(self):
        with self.assertRaises(ValueError):
            contours.keep_layers(field(3, layer("Cntr"))[:-3], {"Cntr"})


class TileIds(unittest.TestCase):
    def test_known_ids(self):
        # From the PMTiles v3 spec examples.
        self.assertEqual(contours.tile_id_to_zxy(0), (0, 0, 0))
        self.assertEqual(contours.tile_id_to_zxy(1), (1, 0, 0))
        self.assertEqual(contours.tile_id_to_zxy(2), (1, 0, 1))
        self.assertEqual(contours.tile_id_to_zxy(3), (1, 1, 1))
        self.assertEqual(contours.tile_id_to_zxy(4), (1, 1, 0))
        self.assertEqual(contours.tile_id_to_zxy(5), (2, 0, 0))

    def test_each_zoom_covers_every_tile_once(self):
        for z in range(4):
            first = sum(4 ** k for k in range(z))
            seen = {contours.tile_id_to_zxy(first + i) for i in range(4 ** z)}
            self.assertEqual(seen, {(z, x, y) for x in range(2 ** z) for y in range(2 ** z)})


def write_pmtiles(path, tiles, meta):
    """A minimal PMTiles v3 file: one root directory, no leaves.
    tiles: [(tile id, run length, gzip bytes)] sorted by id."""
    data, entries = b"", []
    for tid, run, blob in tiles:
        entries.append((tid, run, len(data), len(blob)))
        data += blob
    d = V(len(entries))
    last = 0
    for tid, *_ in entries:
        d += V(tid - last)
        last = tid
    for _, run, *_ in entries:
        d += V(run)
    for *_, size in entries:
        d += V(size)
    for k, (_, _, off, _) in enumerate(entries):
        d += V(0) if k and off == entries[k - 1][2] + entries[k - 1][3] else V(off + 1)
    root = gzip.compress(d)
    metadata = gzip.compress(json.dumps(meta).encode())
    root_off = 127
    meta_off = root_off + len(root)
    tile_off = meta_off + len(metadata)
    header = b"PMTiles" + bytes([3]) + struct.pack(
        "<8Q", root_off, len(root), meta_off, len(metadata), tile_off, 0, tile_off, len(data))
    header += struct.pack("<3Q", len(tiles), len(tiles), len(tiles))
    header += bytes([1, 2, 2, 1])  # clustered, internal gzip, tile gzip, MVT
    header += bytes(127 - len(header))
    Path(path).write_bytes(header + root + metadata + data)


class FilterToMbtiles(unittest.TestCase):
    def test_filters_drops_empty_and_flips_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "in.pmtiles", Path(tmp) / "out.mbtiles"
            write_pmtiles(src, [
                (0, 1, gzip.compress(tile("RdCL"))),             # z0: nothing kept
                (1, 2, gzip.compress(tile("Cntr", "RdCL"))),     # z1 (0,0) and (0,1)
                (4, 1, gzip.compress(tile("Cstline"))),          # z1 (1,0)
            ], {"vector_layers": [{"id": "Cntr"}, {"id": "RdCL"}, {"id": "Cstline"}]})
            count, zmin, zmax = contours.filter_to_mbtiles(
                src, out, {"Cntr", "Cstline"}, [135.2, 34.6, 135.4, 34.8], [34.7, 135.3], "credit")
            self.assertEqual((count, zmin, zmax), (3, 1, 1))
            db = sqlite3.connect(out)
            rows = {(z, x, r): gzip.decompress(b) for z, x, r, b in db.execute("SELECT * FROM tiles")}
            self.assertEqual(rows, {(1, 0, 1): tile("Cntr"), (1, 0, 0): tile("Cntr"),
                                    (1, 1, 1): tile("Cstline")})
            meta = dict(db.execute("SELECT * FROM metadata"))
            db.close()
            self.assertEqual(meta["bounds"], "135.2,34.6,135.4,34.8")
            self.assertEqual(meta["center"], "135.3,34.7,1")
            self.assertEqual(meta["attribution"], "credit")
            self.assertEqual([l["id"] for l in json.loads(meta["json"])["vector_layers"]],
                             ["Cntr", "Cstline"])

    def test_no_data_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.pmtiles"
            write_pmtiles(src, [(0, 1, gzip.compress(tile("RdCL")))], {})
            with self.assertRaises(SystemExit):
                contours.filter_to_mbtiles(src, Path(tmp) / "o.mbtiles", {"Cntr"},
                                           [0, 0, 1, 1], [0.5, 0.5], "x")

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "x.pmtiles"
            src.write_bytes(b"not a pmtiles file" + bytes(200))
            with self.assertRaises(ValueError):
                list(contours.read_pmtiles(src))


if __name__ == "__main__":
    unittest.main()
