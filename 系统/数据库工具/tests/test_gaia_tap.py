"""Gaia TAP 客户端的离线单元测试（不联网）。

重点验证“TAP 静默截断”防护：带 join 的大锥搜索在服务端高负载时可能返回
HTTP 200 + 不完整 CSV；导入器必须用 COUNT 校验，不能把截断结果写进
`provenance=tap` / `complete=true` 的场缓存。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from db.gaia_tap import (
    GaiaTAPError,
    fetch_gaia_cone,
    fetch_gaia_count,
    parse_gaia_csv,
)

_DATA_2 = (
    "source_id,ra,dec\n"
    "1001,269.4485,4.7394\n"
    "1002,269.4486,4.7395\n"
)
_DATA_1 = (
    "source_id,ra,dec\n"
    "1001,269.4485,4.7394\n"
)


def _fake_get_factory(*, count: int, data: str):
    def fake_get(url: str, *, timeout: float) -> bytes:
        query = parse_qs(urlparse(url).query)["QUERY"][0]
        if "COUNT(*)" in query:
            return f"n\n{count}\n".encode()
        return data.encode()

    return fake_get


class GaiaTAPClientTest(unittest.TestCase):
    def test_count_query(self):
        with patch("db.gaia_tap._http_get", _fake_get_factory(count=9603, data="")):
            self.assertEqual(
                fetch_gaia_count(269.452077, 4.693365, 0.25, retries=1), 9603
            )

    def test_complete_cone_is_returned(self):
        with patch("db.gaia_tap._http_get", _fake_get_factory(count=2, data=_DATA_2)):
            rows, raw, query = fetch_gaia_cone(269.452077, 4.693365, 0.25, retries=1)
        self.assertEqual(len(rows), 2)
        self.assertIn("source_id", raw)
        self.assertIn("CIRCLE", query)
        self.assertEqual({r["catalog_id"] for r in rows}, {"1001", "1002"})

    def test_quality_columns_are_parsed(self):
        rows = parse_gaia_csv(
            "source_id,ra,dec,parallax_error,ruwe,astrometric_excess_noise,phot_variable_flag\n"
            "1,10,20,0.04,1.08,0.20,NOT_AVAILABLE\n"
        )
        self.assertEqual(rows[0]["parallax_error"], 0.04)
        self.assertEqual(rows[0]["ruwe"], 1.08)
        self.assertEqual(rows[0]["astrometric_excess_noise"], 0.20)
        self.assertEqual(rows[0]["phot_variable_flag"], "NOT_AVAILABLE")

    def test_truncated_cone_raises_not_silently_partial(self):
        # COUNT=2 但 join 查询只回 1 行 → 必须失败，不能落成“完整场缓存”。
        with patch("db.gaia_tap._http_get", _fake_get_factory(count=2, data=_DATA_1)):
            with self.assertRaises(GaiaTAPError) as ctx:
                fetch_gaia_cone(269.452077, 4.693365, 0.25, retries=1)
        self.assertIn("不完整", str(ctx.exception))

    def test_duplicate_rows_are_deduped_before_count_check(self):
        # LEFT JOIN 若出现重复 source_id，按唯一源数校验，不能把重复当多出来的行。
        duplicate = _DATA_2 + "1001,269.4485,4.7394\n"
        with patch("db.gaia_tap._http_get", _fake_get_factory(count=2, data=duplicate)):
            rows, _, _ = fetch_gaia_cone(269.452077, 4.693365, 0.25, retries=1)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
