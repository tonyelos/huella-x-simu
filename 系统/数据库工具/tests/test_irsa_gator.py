"""IRSA Gator IPAC 解析 / 2MASS-WISE 对回 Gaia 的离线单元测试。"""
from __future__ import annotations

import unittest

from db.irsa_gator import (
    IRSAGatorError,
    enrich_gaia_rows,
    normalize_gator_rows,
    parse_ipac_table,
)

_IPAC = """\\
\\RowsRetrieved =                 2
\\DATABASE= 'test'
|          designation|          ra|         dec|    j_m|    h_m|    k_m| ph_qual|    w1mpro|    w2mpro| ph_qual|
|                 char|      double|      double| double| double| double|    char|    double|    double|    char|
|                     |         deg|         deg|    mag|    mag|    mag|        |       mag|       mag|        |
|                 null|        null|        null|   null|   null|   null|    null|      null|      null|    null|
 17574743+0441461  269.4475605  4.6960872  13.185  13.004  13.857     AAA   14.338  14.871     AA
 J175747.30+044131.1 269.4470860 4.6919801    null    null    null    null  16.092  17.147     AC
"""


class IRSAGatorTest(unittest.TestCase):
    def test_parse_ipac_table(self):
        rows = parse_ipac_table(_IPAC)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["designation"], "17574743+0441461")
        self.assertEqual(rows[0]["j_m"], "13.185")
        self.assertEqual(rows[1]["j_m"], "null")

    def test_parse_detects_truncation(self):
        with self.assertRaises(IRSAGatorError) as ctx:
            parse_ipac_table(_IPAC.replace("RowsRetrieved =                 2", "RowsRetrieved =                 3"))
        self.assertIn("不完整", str(ctx.exception))

    def test_normalize_2mass_wise_real_and_null(self):
        rows = parse_ipac_table(_IPAC)
        tm = normalize_gator_rows("2mass", rows)
        self.assertEqual(len(tm), 2)
        self.assertEqual(tm[0]["jmag"], 13.185)
        self.assertEqual(tm[0]["hmag"], 13.004)
        self.assertEqual(tm[0]["kmag"], 13.857)
        self.assertIsNone(tm[1]["jmag"])  # null 保持 null，不是 0
        wise = normalize_gator_rows("wise", rows)
        self.assertEqual(wise[0]["w1"], 14.338)
        self.assertEqual(wise[0]["w2"], 14.871)
        self.assertEqual(wise[1]["w1"], 16.092)

    def test_enrich_gaia_rows_by_crossmatch_id(self):
        rows = parse_ipac_table(_IPAC)
        photometry = {
            "catalogs": {
                "2mass": normalize_gator_rows("2mass", rows),
                "wise": normalize_gator_rows("wise", rows),
            },
            "errors": {},
        }
        gaia = [
            {"catalog_id": "1", "twomass_id": "17574743+0441461", "wise_id": "J175747.30+044131.1"},
            {"catalog_id": "2", "twomass_id": None, "wise_id": None},
        ]
        stats = enrich_gaia_rows(gaia, photometry)
        self.assertEqual(stats["2mass"]["matched"], 1)
        self.assertEqual(stats["wise"]["matched"], 1)
        self.assertEqual(gaia[0]["jmag"], 13.185)
        self.assertEqual(gaia[0]["w1"], 16.092)
        self.assertIsNone(gaia[1]["jmag"])
        self.assertIsNone(gaia[1]["w1"])


if __name__ == "__main__":
    unittest.main()
