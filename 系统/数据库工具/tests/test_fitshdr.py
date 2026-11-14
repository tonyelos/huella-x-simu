from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db.fitshdr import HeaderError, extract_record, parse_hdus, parse_value
from tests.fixtures import make_header


class ParseValueTest(unittest.TestCase):
    def test_string_with_slash(self):
        self.assertEqual(parse_value("'MJy / sr'           / Unit"), "MJy / sr")

    def test_escaped_quote(self):
        self.assertEqual(parse_value("'O''Brien'"), "O'Brien")

    def test_bool(self):
        self.assertIs(parse_value("T"), True)
        self.assertIs(parse_value("F"), False)

    def test_numbers(self):
        self.assertEqual(parse_value("123"), 123)
        self.assertAlmostEqual(parse_value("-1.25E-3"), -0.00125)
        self.assertAlmostEqual(parse_value("2.5D+02"), 250.0)

    def test_empty(self):
        self.assertIsNone(parse_value(""))


class HeaderExtractTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_header(Path(td) / "a.hdr", "2026W01_1A_0001_1", 2, 150.25, 20.5, 61000.5)
            blob = p.read_bytes()
            hdus = parse_hdus(blob)
            self.assertEqual(len(hdus), 2)
            rec = extract_record(blob, str(p))
            self.assertEqual(rec["obs_id"], "2026W01_1A_0001_1")
            self.assertEqual(rec["detector"], 2)
            self.assertEqual(rec["week"], "2026W01_1A")
            self.assertEqual(rec["field_no"], 1)
            self.assertEqual(rec["repeat_no"], 1)
            self.assertAlmostEqual(rec["ra"], 150.25)
            self.assertAlmostEqual(rec["dec"], 20.5)
            self.assertAlmostEqual(rec["mjd"], 61000.5)
            self.assertAlmostEqual(rec["pixel_scale_arcsec"], 0.0017 * 3600, places=6)
            self.assertAlmostEqual(rec["sgt_lat"], 10.0)
            self.assertAlmostEqual(rec["sgt_lon"], 20.0)

    def test_missing_obsid(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.hdr"
            p.write_bytes(b" " * 2880 + b"END".ljust(80) + b" " * 2800)
            with self.assertRaises(HeaderError):
                extract_record(p.read_bytes())


if __name__ == "__main__":
    unittest.main()
