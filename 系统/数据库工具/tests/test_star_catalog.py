"""阶段 B/D1 星表服务的离线回归测试（不联网、不依赖 7 MB 真实缓存）。

测试数据是 `tests/fixtures/gaia_dr3_sample.csv`：真实 Gaia DR3 TAP 结果的小样本
（250 行 / 7 个预设场；行数由 N_FIXTURE_ROWS 动态校验），每次测试导入临时 SQLite。重点覆盖：

- schema/列、release 版本/许可/参考历元；
- Barnard 锥搜索与 known match：fixture 源按 PM 传播到 MJD 61210；
- 0.216″ 对 → ambiguous（不是 known，也不是新星）；
- fixture 导入、HEALPix 参考值；
- CATALOG_UNAVAILABLE：缺库/缺天区/坏库；
- HTTP health/cone/match、catalog_match 持久化。
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import catalog_server
from catalog_server import make_server
from db.gaia_tap import (
    GAIA_DR3_CITATION,
    GAIA_DR3_LICENSE,
    GAIA_TAP_SYNC,
    fixture_meta,
    read_fixture_rows,
)
from db.healpix import ang2pix_nest
from db.star_catalog import (
    CatalogError,
    CatalogUnavailable,
    CatalogValidationError,
    StarCatalog,
    angsep_arcsec,
    propagate_pm,
)
from db.star_config import (
    CATALOG_API_VERSION,
    CATALOG_DOC,
    DEFAULT_GAIA_FIXTURE,
    GAIA_EPOCH_MJD,
    GAIA_EPOCH_REF,
    STAR_SCHEMA_VERSION,
)
from db.star_schema import init_star_db
from tools import import_gaia

FIXTURE = DEFAULT_GAIA_FIXTURE
N_FIXTURE_ROWS = len(read_fixture_rows(FIXTURE))
BARNARD_SOURCE_ID = "4472832130942575872"  # Gaia DR3 Barnard's Star，pmdec≈10362 mas/yr
PAIR_A = "4472820208110875136"  # 0.216″ 对（fixture 最近对）
PAIR_B = "4472820212403005440"
OBS_MJD = 61210.0
BARNARD_J2000 = (269.452077, 4.693365)
# fixture 源按 PM 从 J2016.0 传播到 MJD 61210 的期望位置（独立于被测代码硬编码）。
BARNARD_PROPAGATED = (269.4461649815, 4.7695363257)


def build_fixture_db(db_path: Path) -> None:
    """用 CLI 的离线路径建一个 fixture 星表库（测试里只写临时目录）。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = import_gaia.main(
            ["--offline", "--rebuild", "--db", str(db_path)]
        )
    if rc != 0:
        raise AssertionError(f"fixture 导入失败 rc={rc}: {err.getvalue()}")


def http_request(port: int, method: str, path: str, payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def http_json(port: int, method: str, path: str, payload: dict | None = None):
    code, raw, _ = http_request(port, method, path, payload)
    return code, json.loads(raw.decode("utf-8"))


@contextlib.contextmanager
def running_server(db_path: Path, *, docs_path: Path | None = None):
    httpd = make_server(db_path, port=0, docs_path=docs_path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class StarCatalogDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.db = Path(cls.td.name) / "star_catalog.sqlite"
        build_fixture_db(cls.db)
        cls.cat = StarCatalog(cls.db, readonly=True)
        cls.rows = read_fixture_rows(FIXTURE)
        cls.by_id = {r["catalog_id"]: r for r in cls.rows}
        cls.release = cls.cat.get_release("gaia", "dr3")

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()
        cls.td.cleanup()

    # ------------------------------------------------------------------ schema/版本
    def test_schema_tables_and_columns(self):
        expected = {
            "star_meta": {"key", "value"},
            "catalog_release": {
                "release_id", "release_key", "catalog", "version", "epoch_ref",
                "epoch_mjd", "imported_at", "license", "citation", "source_url", "provenance",
                "cache_radius_deg", "mag_limit", "n_sources", "notes",
            },
            "catalog_field": {
                "release_id", "field_id", "field_name", "ra", "dec",
                "radius_deg", "n_sources", "provenance", "photometry_json",
                "imported_at",
            },
            "catalog_source": {
                "source_id", "release_id", "field_id", "catalog_id", "ra", "dec",
                "pmra", "pmdec", "parallax", "ra_error", "dec_error",
                "pmra_error", "pmdec_error", "parallax_error", "ruwe",
                "astrometric_excess_noise", "phot_variable_flag", "quality",
                "gmag", "bp_rp", "jmag", "hmag",
                "kmag", "w1", "w2", "twomass_id", "wise_id", "epoch_ref",
                "healpix", "object_type", "provenance", "created_at",
            },
            "catalog_match": {
                "match_id", "analysis_id", "detection_id", "release_id",
                "source_id", "sep_arcsec", "pm_residual", "pa_residual",
                "ambiguity", "confidence", "detection_ra", "detection_dec",
                "detection_mjd", "created_at",
            },
            "catalog_import_run": {
                "run_id", "release_id", "started_at", "finished_at", "status",
                "mode", "n_fields", "n_sources", "details_json", "errors_json",
            },
        }
        for table, required in expected.items():
            cols = {
                r["name"]
                for r in self.cat.conn.execute(f"PRAGMA table_info({table})")
            }
            self.assertTrue(required <= cols, f"{table} 缺列: {required - cols}")

        view_cols = {
            r["name"]
            for r in self.cat.conn.execute("PRAGMA table_info(v_catalog_source)")
        }
        self.assertIn("2mass", view_cols)
        self.assertIn("wise", view_cols)
        self.assertIn("source_id", view_cols)
        self.assertIn("release", {
            r["name"]
            for r in self.cat.conn.execute("PRAGMA table_info(v_catalog_release)")
        })

        self.assertEqual(self.cat.schema_version(), STAR_SCHEMA_VERSION)
        meta = dict(
            self.cat.conn.execute("SELECT key, value FROM star_meta").fetchall()
        )
        self.assertEqual(meta["schema_version"], str(STAR_SCHEMA_VERSION))

    def test_release_version_license_epoch(self):
        rel = self.release
        self.assertEqual(rel["release"], "gaia-dr3")
        self.assertEqual(rel["catalog"], "gaia")
        self.assertEqual(rel["version"], "dr3")
        self.assertEqual(rel["epoch_ref"], GAIA_EPOCH_REF)
        self.assertEqual(rel["epoch_ref"], "J2016.0")
        self.assertEqual(rel["epoch_mjd"], GAIA_EPOCH_MJD)
        self.assertEqual(rel["epoch_mjd"], 57388.5)
        self.assertEqual(rel["license"], GAIA_DR3_LICENSE)
        self.assertEqual(rel["license"], "CC BY-NC 3.0 IGO")
        self.assertIn("Gaia Collaboration", rel["citation"])
        self.assertIn("10.1051/0004-6361/202243940", rel["citation"])
        self.assertEqual(rel["source_url"], GAIA_TAP_SYNC)
        self.assertEqual(rel["provenance"], "fixture")
        self.assertEqual(rel["cache_radius_deg"], 0.25)
        self.assertEqual(rel["mag_limit"], 20.0)
        self.assertEqual(rel["n_sources"], N_FIXTURE_ROWS)

        releases = self.cat.releases()
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["n_sources_actual"], N_FIXTURE_ROWS)
        self.assertEqual(releases[0]["n_fields"], 7)
        fields = self.cat.fields(releases[0]["release_id"])
        self.assertEqual(len(fields), 7)
        self.assertEqual(
            {f["field_id"] for f in fields},
            {"barnard", "epsind_b", "wise0855", "rw_aur", "r_dra", "m101", "campo_a"},
        )
        self.assertTrue(all(f["provenance"] == "fixture" for f in fields))

        run = self.cat.conn.execute(
            "SELECT * FROM catalog_import_run ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["mode"], "fixture")
        self.assertEqual(run["n_sources"], N_FIXTURE_ROWS)
        self.assertEqual(run["n_fields"], 7)

        health = self.cat.health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["service"], "star-catalog")
        self.assertEqual(health["schema_version"], CATALOG_API_VERSION)
        self.assertEqual(health["star_schema_version"], STAR_SCHEMA_VERSION)
        self.assertEqual(health["db_schema_version"], STAR_SCHEMA_VERSION)
        self.assertEqual(health["counts"]["sources"], N_FIXTURE_ROWS)

    def test_fixture_csv_is_real_gaia_sample_with_license(self):
        rows = self.rows
        self.assertEqual(len(rows), N_FIXTURE_ROWS)
        self.assertEqual(len({r["field_id"] for r in rows}), 7)
        self.assertTrue(all(isinstance(r["catalog_id"], str) for r in rows))
        # Gaia source_id 是 64 位整数；必须以字符串过 API（D-09）。
        self.assertTrue(all(r["catalog_id"].isdigit() for r in rows))
        self.assertTrue(all(int(r["catalog_id"]) > 2**53 for r in rows))
        self.assertTrue(all(r["ref_epoch"] == 2016.0 for r in rows))

        meta = fixture_meta(FIXTURE)
        self.assertEqual(meta["catalog"], "gaia")
        self.assertEqual(meta["version"], "dr3")
        self.assertEqual(meta["license"], "CC BY-NC 3.0 IGO")
        self.assertEqual(meta["citation"], GAIA_DR3_CITATION)
        self.assertEqual(meta["tap_url"], GAIA_TAP_SYNC)
        self.assertEqual(meta["archive_url"], "https://gea.esac.esa.int/archive/")
        self.assertRegex(meta["fetched_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(meta["n_fixture_rows"], N_FIXTURE_ROWS)
        self.assertEqual(sum(meta["field_counts"].values()), 17_679)
        # 不得把 fixture 样本说成全量/完整星表。
        self.assertIn("不完整", meta["note"])
        self.assertIn("仅", meta["note"])
        self.assertNotIn("OSI", meta["license"])
        self.assertFalse(meta["complete"])
        self.assertEqual(meta["usage"], "tests_only")

    def test_fixture_import_cli_rebuilds_temp_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "fresh.sqlite"
            build_fixture_db(db)
            with StarCatalog(db) as cat:
                rel = cat.get_release("gaia")
                self.assertEqual(rel["provenance"], "fixture")
                self.assertEqual(rel["n_sources"], N_FIXTURE_ROWS)
                self.assertEqual(rel["license"], "CC BY-NC 3.0 IGO")
                self.assertIn("Not a full-sky catalog", rel["notes"])
                self.assertEqual(cat.health()["counts"]["fields"], 7)

    # ------------------------------------------------------------------ HEALPix
    def test_healpix_reference_and_import_consistency(self):
        # HEALPix nside=1 的标准 NESTED 面编号：4 个赤道面 + 南北极面。
        self.assertEqual(ang2pix_nest(1, 0.0, 90.0), 0)
        self.assertEqual(ang2pix_nest(1, 0.0, 0.0), 4)
        self.assertEqual(ang2pix_nest(1, 90.0, 0.0), 5)
        self.assertEqual(ang2pix_nest(1, 180.0, 0.0), 6)
        self.assertEqual(ang2pix_nest(1, 270.0, 0.0), 7)
        self.assertEqual(ang2pix_nest(1, 0.0, -90.0), 8)
        # NESTED 子像素：nside=64 的像素号 // 4096 必须等于 nside=1 像素号。
        for ra, dec in [
            (0.0, 90.0),
            (0.0, 0.0),
            (45.0, 45.0),
            (269.44850252543836, 4.739420051112412),
            (133.795129, -7.245147),
        ]:
            p64 = ang2pix_nest(64, ra, dec)
            self.assertEqual(p64 // (64 * 64), ang2pix_nest(1, ra, dec))
            self.assertGreaterEqual(p64, 0)
            self.assertLess(p64, 12 * 64 * 64)
        with self.assertRaises(ValueError):
            ang2pix_nest(63, 0.0, 0.0)
        with self.assertRaises(ValueError):
            ang2pix_nest(64, 0.0, 91.0)

        src = self.by_id[BARNARD_SOURCE_ID]
        row = self.cat.conn.execute(
            "SELECT healpix FROM catalog_source WHERE catalog_id=?", (BARNARD_SOURCE_ID,)
        ).fetchone()
        self.assertEqual(row["healpix"], ang2pix_nest(64, src["ra"], src["dec"]))

    # ------------------------------------------------------------------ Barnard
    def test_barnard_pm_propagation_and_cone(self):
        src = self.by_id[BARNARD_SOURCE_ID]
        self.assertAlmostEqual(src["ra"], 269.448503, places=6)  # Gaia DR3 J2016 坐标
        self.assertAlmostEqual(src["dec"], 4.739420, places=6)
        self.assertGreater(src["pmdec"], 10_000.0)  # 约 10.36″/yr

        dt_years = (OBS_MJD - GAIA_EPOCH_MJD) / 365.25
        ra_p, dec_p = propagate_pm(
            src["ra"], src["dec"], src["pmra"], src["pmdec"], dt_years
        )
        self.assertAlmostEqual(ra_p, BARNARD_PROPAGATED[0], places=6)
        self.assertAlmostEqual(dec_p, BARNARD_PROPAGATED[1], places=6)

        out = self.cat.cone(ra_p, dec_p, 0.001, epoch=OBS_MJD, limit=10)
        self.assertEqual(out["release"], "gaia-dr3")
        self.assertEqual(out["version"], "dr3")
        self.assertEqual(out["epoch_ref"], "J2016.0")
        self.assertEqual(out["epoch_ref_mjd"], 57388.5)
        self.assertEqual(out["license"], "CC BY-NC 3.0 IGO")
        self.assertIn("Gaia Collaboration", out["citation"])
        self.assertEqual(out["catalog_status"], "partial")  # fixture 小样本
        self.assertTrue(out["cache_limited"])
        self.assertEqual(out["count"], 1)
        source = out["sources"][0]
        self.assertEqual(source["catalog_id"], BARNARD_SOURCE_ID)
        self.assertEqual(source["source_id"], f"gaia-dr3:{BARNARD_SOURCE_ID}")
        self.assertLess(source["sep_arcsec"], 0.01)
        self.assertAlmostEqual(source["ra"], src["ra"], places=9)  # 原生历元行仍保留
        self.assertAlmostEqual(source["dec"], src["dec"], places=9)
        self.assertAlmostEqual(source["ra_propagated"], ra_p, places=6)
        self.assertAlmostEqual(source["dec_propagated"], dec_p, places=6)
        self.assertEqual(source["epoch"], OBS_MJD)
        self.assertEqual(source["object_type"], "star")
        self.assertAlmostEqual(source["ruwe"], 1.0848505, places=6)
        self.assertAlmostEqual(source["astrometric_excess_noise"], 0.20481846, places=7)
        self.assertEqual(source["phot_variable_flag"], "NOT_AVAILABLE")
        self.assertGreater(source["parallax_error"], 0.0)
        self.assertFalse(source["ambiguous"])

        # 用 J2000 目标坐标 + 传播历元 + 大锥：Barnard 应出现在约 275″ 处。
        wide = self.cat.cone(*BARNARD_J2000, 0.1, epoch=OBS_MJD, limit=500)
        hits = {s["catalog_id"]: s for s in wide["sources"]}
        self.assertIn(BARNARD_SOURCE_ID, hits)
        self.assertGreater(hits[BARNARD_SOURCE_ID]["sep_arcsec"], 270.0)
        self.assertLess(hits[BARNARD_SOURCE_ID]["sep_arcsec"], 280.0)
        self.assertEqual(wide["coverage"]["field_id"], "barnard")

    def test_barnard_known_match_at_epoch(self):
        src = self.by_id[BARNARD_SOURCE_ID]
        dt_years = (OBS_MJD - GAIA_EPOCH_MJD) / 365.25
        ra_p, dec_p = propagate_pm(
            src["ra"], src["dec"], src["pmra"], src["pmdec"], dt_years
        )
        out = self.cat.match(
            [
                {
                    "detection_id": "barnard-known",
                    "ra": ra_p,
                    "dec": dec_p,
                    "mjd": OBS_MJD,
                    "sigma_arcsec": 0.3,
                }
            ],
            radius_arcsec=1.0,
        )
        self.assertEqual(out["n_detections"], 1)
        self.assertEqual(out["n_matched"], 1)
        self.assertEqual(out["n_unmatched"], 0)
        self.assertEqual(out["n_ambiguous"], 0)
        self.assertEqual(out["catalog_status"], "partial")  # fixture 小样本
        self.assertTrue(out["cache_limited"])
        item = out["matches"][0]
        self.assertEqual(item["status"], "single_candidate")
        self.assertFalse(item["ambiguous"])
        self.assertEqual(item["n_candidates"], 1)
        self.assertEqual(item["catalog_status"], "partial")
        self.assertEqual(item["coverage"]["gaia"]["field_id"], "barnard")
        candidate = item["candidates"][0]
        self.assertEqual(candidate["catalog_id"], BARNARD_SOURCE_ID)
        self.assertEqual(candidate["source_id"], f"gaia-dr3:{BARNARD_SOURCE_ID}")
        self.assertLess(candidate["sep_arcsec"], 0.01)
        self.assertGreater(candidate["confidence"], 0.99)
        self.assertAlmostEqual(candidate["ra_propagated"], ra_p, places=6)
        # B 不做科学分类：只给候选，不输出 known_star / 新星结论。
        self.assertNotIn("known_star", candidate)
        self.assertNotIn("known_star", item)
        self.assertNotIn("classification", item)

        # 检测 PM 与星表 PM 一致时给出行星表残差（供 D 的运动一致性判断）。
        out2 = self.cat.match(
            [
                {
                    "detection_id": "barnard-known-pm",
                    "ra": ra_p,
                    "dec": dec_p,
                    "mjd": OBS_MJD,
                    "sigma_arcsec": 0.3,
                    "pmra_mas_yr": src["pmra"],
                    "pmdec_mas_yr": src["pmdec"],
                }
            ],
            radius_arcsec=1.0,
        )
        cand2 = out2["matches"][0]["candidates"][0]
        self.assertLess(cand2["pm_residual"], 0.01)
        self.assertLess(abs(cand2["pa_residual"]), 0.01)

    def test_gaia_quality_flags_and_2mass_wise(self):
        src = self.by_id[BARNARD_SOURCE_ID]
        self.assertAlmostEqual(src["ruwe"], 1.0848505, places=6)
        self.assertAlmostEqual(src["astrometric_excess_noise"], 0.20481846, places=7)
        self.assertEqual(src["phot_variable_flag"], "NOT_AVAILABLE")
        self.assertGreater(src["parallax_error"], 0.0)

        row = self.cat.conn.execute(
            """SELECT ruwe, astrometric_excess_noise, phot_variable_flag,
                      parallax_error, jmag, hmag, kmag, w1, w2
               FROM catalog_source WHERE catalog_id=?""",
            (BARNARD_SOURCE_ID,),
        ).fetchone()
        self.assertAlmostEqual(row["ruwe"], src["ruwe"], places=9)
        self.assertAlmostEqual(
            row["astrometric_excess_noise"], src["astrometric_excess_noise"], places=9
        )
        self.assertEqual(row["phot_variable_flag"], src["phot_variable_flag"])
        self.assertAlmostEqual(row["parallax_error"], src["parallax_error"], places=9)

        # 有真实 IRSA Gator 2MASS/WISE 值的行：DB 必须与 fixture 的真实值一致。
        photo = next(
            r for r in self.rows
            if r.get("jmag") is not None and r.get("w1") is not None
        )
        prow = self.cat.conn.execute(
            "SELECT jmag, hmag, kmag, w1, w2 FROM catalog_source WHERE catalog_id=?",
            (photo["catalog_id"],),
        ).fetchone()
        for key in ("jmag", "hmag", "kmag", "w1", "w2"):
            if photo.get(key) is not None:
                self.assertAlmostEqual(prow[key], photo[key], places=6)

        # null 边界：Barnard 本身没有 2MASS/WISE 交叉证认，全部为 null，不是 0。
        self.assertIsNone(src["twomass_id"])
        self.assertIsNone(src["wise_id"])
        self.assertIsNone(row["jmag"])
        self.assertIsNone(row["hmag"])
        self.assertIsNone(row["kmag"])
        self.assertIsNone(row["w1"])
        self.assertIsNone(row["w2"])

        fields = self.cat.fields(self.release["release_id"])
        ph = {f["field_id"]: f["photometry"] for f in fields}
        self.assertEqual(ph["barnard"]["status"], "fixture_sample")
        self.assertGreater(ph["barnard"]["2mass"]["j_available"], 0)
        self.assertGreater(ph["barnard"]["wise"]["w1_available"], 0)

    def test_schema_migration_from_v1(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "v1.sqlite"
            init_star_db(db)
            conn = sqlite3.connect(db)
            try:
                conn.execute("DROP VIEW IF EXISTS v_catalog_source")
                conn.execute("DROP VIEW IF EXISTS v_catalog_release")
                conn.execute("DROP INDEX IF EXISTS idx_catalog_source_field")
                conn.execute("DROP TABLE IF EXISTS target_name")
                for col in (
                    "parallax_error",
                    "ruwe",
                    "astrometric_excess_noise",
                    "phot_variable_flag",
                    "field_id",
                    "quality",
                ):
                    conn.execute(f"ALTER TABLE catalog_source DROP COLUMN {col}")
                conn.execute("ALTER TABLE catalog_field DROP COLUMN photometry_json")
                conn.execute(
                    "UPDATE star_meta SET value='2' WHERE key='schema_version'"
                )
                conn.commit()
            finally:
                conn.close()

            init_star_db(db)  # 应幂等迁移，不删已有库
            with StarCatalog(db) as cat:
                self.assertEqual(cat.schema_version(), STAR_SCHEMA_VERSION)
                cols = {
                    r["name"]
                    for r in cat.conn.execute("PRAGMA table_info(catalog_source)")
                }
                self.assertTrue(
                    {"parallax_error", "ruwe", "astrometric_excess_noise", "phot_variable_flag"} <= cols
                )
                field_cols = {
                    r["name"]
                    for r in cat.conn.execute("PRAGMA table_info(catalog_field)")
                }
                self.assertIn("photometry_json", field_cols)
                # v3 迁移按最近场心回填 field_id，测试 fixture 的 250 行都应归场。
                nulls = cat.conn.execute(
                    "SELECT COUNT(*) FROM catalog_source WHERE field_id IS NULL"
                ).fetchone()[0]
                self.assertEqual(nulls, 0)
                self.assertEqual(
                    cat.conn.execute("SELECT COUNT(*) FROM target_name").fetchone()[0],
                    7,
                )

    def test_ambiguous_pair_0216_arcsec(self):
        a = self.by_id[PAIR_A]
        b = self.by_id[PAIR_B]
        sep = angsep_arcsec(a["ra"], a["dec"], b["ra"], b["dec"])
        self.assertAlmostEqual(sep, 0.21627, places=4)
        self.assertLess(sep, 0.22)

        out = self.cat.match(
            [
                {
                    "detection_id": "pair-0216",
                    "ra": a["ra"],
                    "dec": a["dec"],
                    "mjd": GAIA_EPOCH_MJD,
                    "sigma_arcsec": 0.05,
                }
            ],
            radius_arcsec=0.5,
        )
        item = out["matches"][0]
        self.assertEqual(item["status"], "ambiguous")
        self.assertTrue(item["ambiguous"])
        self.assertEqual(item["n_candidates"], 2)
        self.assertEqual(out["n_ambiguous"], 1)
        ids = [c["catalog_id"] for c in item["candidates"]]
        self.assertEqual(ids, [PAIR_A, PAIR_B])
        seps = [c["sep_arcsec"] for c in item["candidates"]]
        self.assertAlmostEqual(seps[0], 0.0, places=6)
        self.assertAlmostEqual(seps[1], 0.21627, places=4)

        # 锥搜索同样把同像元（6.15″）内的两颗源都标 ambiguous。
        cone = self.cat.cone(a["ra"], a["dec"], 0.0005, epoch=GAIA_EPOCH_MJD, limit=10)
        self.assertEqual(cone["count"], 2)
        for source in cone["sources"]:
            self.assertTrue(source["ambiguous"])
            self.assertEqual(source["n_within_ambiguity_arcsec"], 2)
        self.assertEqual(
            {s["catalog_id"] for s in cone["sources"]}, {PAIR_A, PAIR_B}
        )

    def test_empty_result_is_not_unknown(self):
        # 场内空白位置：查询成功但 0 行；不能写成新星/未分类。
        out = self.cat.match(
            [
                {
                    "detection_id": "blank",
                    "ra": 269.48,
                    "dec": 4.72,
                    "mjd": GAIA_EPOCH_MJD,
                    "sigma_arcsec": 1.0,
                }
            ],
            radius_arcsec=3.0,
        )
        item = out["matches"][0]
        self.assertEqual(item["status"], "no_match")
        self.assertEqual(item["n_candidates"], 0)
        self.assertEqual(out["n_unmatched"], 1)
        self.assertIn("不等于新恒星", item["note"])
        self.assertIn("D 阶段", item["note"])

        cone = self.cat.cone(269.48, 4.72, 0.001, epoch=GAIA_EPOCH_MJD)
        self.assertEqual(cone["count"], 0)
        self.assertEqual(cone["returned"], 0)
        self.assertIn("不是新恒星", cone["note"])

    # ------------------------------------------------------------------ 不可用
    def test_out_of_cache_field_raises_catalog_unavailable(self):
        with self.assertRaises(CatalogUnavailable) as ctx:
            self.cat.cone(0.0, 0.0, 0.01)
        self.assertEqual(ctx.exception.reason, "no_cached_field")
        self.assertEqual(ctx.exception.as_dict()["code"], "CATALOG_UNAVAILABLE")

        with self.assertRaises(CatalogUnavailable) as ctx:
            self.cat.match([{"ra": 0.0, "dec": 0.0, "sigma_arcsec": 1.0}])
        self.assertEqual(ctx.exception.reason, "no_cached_field")

    def test_validation_errors(self):
        with self.assertRaises(CatalogValidationError):
            self.cat.cone(0.0, 0.0, 0.0)
        with self.assertRaises(CatalogValidationError):
            self.cat.match([])
        with self.assertRaises(CatalogValidationError):
            self.cat.match([{"ra": 269.45, "dec": 91.0}])
        with self.assertRaises(CatalogValidationError):
            self.cat.match(
                [{"ra": 269.45, "dec": 4.69}], persist=True, analysis_id=None
            )
        with self.assertRaises(CatalogError):
            self.cat.match(
                [{"ra": 269.45, "dec": 4.69}],
                persist=True,
                analysis_id="readonly-test",
            )

    # ------------------------------------------------------------------ 持久化
    def test_catalog_match_persistence(self):
        a = self.by_id[PAIR_A]
        with StarCatalog(self.db, readonly=False) as cat:
            out = cat.match(
                [
                    {
                        "detection_id": "pair-0216",
                        "ra": a["ra"],
                        "dec": a["dec"],
                        "mjd": GAIA_EPOCH_MJD,
                        "sigma_arcsec": 0.05,
                    }
                ],
                radius_arcsec=0.5,
                persist=True,
                analysis_id="analysis:pair-0216",
            )
            self.assertEqual(out["persisted"], 2)
            rows = cat.conn.execute(
                "SELECT * FROM catalog_match WHERE analysis_id=? ORDER BY match_id",
                ("analysis:pair-0216",),
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["source_id"] for r in rows}, {
                f"gaia-dr3:{PAIR_A}", f"gaia-dr3:{PAIR_B}"
            })
            self.assertTrue(all(r["release_id"] == self.release["release_id"] for r in rows))
            self.assertTrue(all(r["ambiguity"] == 1 for r in rows))
            self.assertTrue(all(r["detection_id"] == "pair-0216" for r in rows))
            self.assertTrue(all(r["detection_mjd"] == GAIA_EPOCH_MJD for r in rows))
            seps = sorted(r["sep_arcsec"] for r in rows)
            self.assertAlmostEqual(seps[0], 0.0, places=6)
            self.assertAlmostEqual(seps[1], 0.21627, places=4)
            self.assertTrue(all(r["confidence"] is not None for r in rows))

            # 幂等：同一 analysis_id 整批重放不会累积重复行。
            out_again = cat.match(
                [
                    {
                        "detection_id": "pair-0216",
                        "ra": a["ra"],
                        "dec": a["dec"],
                        "mjd": GAIA_EPOCH_MJD,
                        "sigma_arcsec": 0.05,
                    }
                ],
                radius_arcsec=0.5,
                persist=True,
                analysis_id="analysis:pair-0216",
            )
            self.assertEqual(out_again["persisted"], 2)
            repeated = cat.conn.execute(
                "SELECT COUNT(*) FROM catalog_match WHERE analysis_id=?",
                ("analysis:pair-0216",),
            ).fetchone()[0]
            self.assertEqual(repeated, 2)

            # 回滚：伪 source 让 INSERT 失败；上一版 2 行必须原样保留。
            with self.assertRaises(CatalogError):
                cat.record_matches(
                    "analysis:pair-0216",
                    [
                        {
                            "index": 0,
                            "ambiguous": False,
                            "candidates": [
                                {"source_id": "gaia-dr3:does-not-exist", "sep_arcsec": 1.0}
                            ],
                        }
                    ],
                )
            after_rollback = cat.conn.execute(
                "SELECT COUNT(*) FROM catalog_match WHERE analysis_id=?",
                ("analysis:pair-0216",),
            ).fetchone()[0]
            self.assertEqual(after_rollback, 2)

            # 没有候选时清空该 analysis 的旧行（整批重放语义）。
            self.assertEqual(cat.record_matches("analysis:empty", [{"index": 0, "candidates": []}]), 0)
            cat.conn.execute(
                "DELETE FROM catalog_match WHERE analysis_id LIKE 'analysis:%'"
            )
            left = cat.conn.execute(
                "SELECT COUNT(*) FROM catalog_match WHERE analysis_id LIKE 'analysis:%'"
            ).fetchone()[0]
            self.assertEqual(left, 0)


class StarCatalogHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.db = Path(cls.td.name) / "star_catalog.sqlite"
        build_fixture_db(cls.db)
        cls.port, cls.httpd = None, None
        cls._cm = running_server(cls.db, docs_path=CATALOG_DOC)
        cls.port = cls._cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        cls.td.cleanup()

    def test_health_releases_fields_stats(self):
        code, data = http_json(self.port, "GET", "/api/v1/health")
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["service"], "star-catalog")
        self.assertEqual(data["schema_version"], CATALOG_API_VERSION)
        self.assertEqual(data["counts"]["sources"], N_FIXTURE_ROWS)
        self.assertEqual(data["releases"][0]["license"], "CC BY-NC 3.0 IGO")

        code, data = http_json(self.port, "GET", "/api/v1/catalog/releases")
        self.assertEqual(code, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["releases"][0]["release"], "gaia-dr3")

        code, data = http_json(
            self.port, "GET", "/api/v1/catalog/fields?catalog=gaia&version=dr3"
        )
        self.assertEqual(code, 200)
        self.assertEqual(data["count"], 7)

        code, data = http_json(self.port, "GET", "/api/v1/catalog/stats")
        self.assertEqual(code, 200)
        self.assertEqual(data["counts"]["sources"], N_FIXTURE_ROWS)

    def test_http_cone_barnard(self):
        src = read_fixture_rows(FIXTURE)
        barnard = next(r for r in src if r["catalog_id"] == BARNARD_SOURCE_ID)
        ra_p, dec_p = propagate_pm(
            barnard["ra"],
            barnard["dec"],
            barnard["pmra"],
            barnard["pmdec"],
            (OBS_MJD - GAIA_EPOCH_MJD) / 365.25,
        )
        code, data = http_json(
            self.port,
            "GET",
            f"/api/v1/catalog/cone?ra={ra_p:.9f}&dec={dec_p:.9f}"
            f"&radius_deg=0.001&epoch={OBS_MJD}&limit=5",
        )
        self.assertEqual(code, 200)
        self.assertEqual(data["release"], "gaia-dr3")
        self.assertEqual(data["license"], "CC BY-NC 3.0 IGO")
        self.assertEqual(data["epoch_ref_mjd"], 57388.5)
        self.assertEqual(data["catalog_status"], "partial")
        self.assertEqual(data["count"], 1)
        source = data["sources"][0]
        self.assertEqual(source["catalog_id"], BARNARD_SOURCE_ID)
        self.assertLess(source["sep_arcsec"], 0.01)
        self.assertAlmostEqual(source["ruwe"], 1.0848505, places=6)
        self.assertAlmostEqual(source["astrometric_excess_noise"], 0.20481846, places=7)
        self.assertEqual(source["phot_variable_flag"], "NOT_AVAILABLE")
        self.assertGreater(source["parallax_error"], 0.0)
        self.assertIsNone(source["jmag"])
        self.assertIsNone(source["w1"])

    def test_http_cone_unavailable_and_bad_request(self):
        code, data = http_json(
            self.port, "GET", "/api/v1/catalog/cone?ra=0&dec=0&radius_deg=0.01"
        )
        self.assertEqual(code, 503)
        self.assertEqual(data["code"], "CATALOG_UNAVAILABLE")
        self.assertEqual(data["reason"], "no_cached_field")

        code, data = http_json(self.port, "GET", "/api/v1/catalog/cone?dec=4")
        self.assertEqual(code, 400)
        self.assertEqual(data["code"], "BAD_REQUEST")

        code, data = http_json(
            self.port, "GET", "/api/v1/catalog/cone?ra=1&dec=2&radius_deg=0"
        )
        self.assertEqual(code, 400)
        self.assertEqual(data["code"], "BAD_REQUEST")

        code, data = http_json(
            self.port, "GET", "/api/v1/catalog/cone?ra=nan&dec=2"
        )
        self.assertEqual(code, 400)

    def test_http_match_known_and_ambiguous(self):
        rows = read_fixture_rows(FIXTURE)
        barnard = next(r for r in rows if r["catalog_id"] == BARNARD_SOURCE_ID)
        ra_p, dec_p = propagate_pm(
            barnard["ra"],
            barnard["dec"],
            barnard["pmra"],
            barnard["pmdec"],
            (OBS_MJD - GAIA_EPOCH_MJD) / 365.25,
        )
        code, data = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {
                "detections": [
                    {
                        "detection_id": "http-barnard",
                        "ra": ra_p,
                        "dec": dec_p,
                        "mjd": OBS_MJD,
                        "sigma_arcsec": 0.3,
                    }
                ],
                "radius_arcsec": 1.0,
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(data["releases"][0]["license"], "CC BY-NC 3.0 IGO")
        item = data["matches"][0]
        self.assertEqual(item["status"], "single_candidate")
        candidate = item["candidates"][0]
        self.assertEqual(candidate["catalog_id"], BARNARD_SOURCE_ID)
        self.assertAlmostEqual(candidate["ruwe"], 1.0848505, places=6)
        self.assertEqual(candidate["phot_variable_flag"], "NOT_AVAILABLE")
        self.assertIsNone(candidate["jmag"])

        pair = next(r for r in rows if r["catalog_id"] == PAIR_A)
        code, data = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {
                "detections": [
                    {
                        "detection_id": "http-pair",
                        "ra": pair["ra"],
                        "dec": pair["dec"],
                        "mjd": GAIA_EPOCH_MJD,
                        "sigma_arcsec": 0.05,
                    }
                ],
                "radius_arcsec": 0.5,
            },
        )
        self.assertEqual(code, 200)
        item = data["matches"][0]
        self.assertEqual(item["status"], "ambiguous")
        self.assertEqual(item["n_candidates"], 2)
        self.assertAlmostEqual(
            sorted(c["sep_arcsec"] for c in item["candidates"])[1], 0.21627, places=4
        )

    def test_http_match_unavailable_and_method(self):
        code, data = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {"detections": [{"ra": 0.0, "dec": 0.0, "sigma_arcsec": 1.0}]},
        )
        self.assertEqual(code, 503)
        self.assertEqual(data["code"], "CATALOG_UNAVAILABLE")
        self.assertEqual(data["reason"], "no_cached_field")

        code, data = http_json(self.port, "GET", "/api/v1/catalog/match")
        self.assertEqual(code, 405)
        self.assertEqual(data["code"], "METHOD_NOT_ALLOWED")

        code, data = http_json(self.port, "GET", "/api/v1/nope")
        self.assertEqual(code, 404)
        self.assertEqual(data["code"], "NOT_FOUND")

    def test_http_match_persist_and_stats(self):
        pair = next(r for r in read_fixture_rows(FIXTURE) if r["catalog_id"] == PAIR_A)
        code, data = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {
                "detections": [
                    {
                        "detection_id": "http-persist",
                        "ra": pair["ra"],
                        "dec": pair["dec"],
                        "mjd": GAIA_EPOCH_MJD,
                        "sigma_arcsec": 0.05,
                    }
                ],
                "radius_arcsec": 0.5,
                "persist": True,
                "analysis_id": "analysis:http-persist",
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(data["persisted"], 2)
        conn = sqlite3.connect(self.db)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM catalog_match WHERE analysis_id=?",
                ("analysis:http-persist",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 2)

        # 同一 analysis_id 重放：persisted 仍为 2，不累积重复行。
        code, data_again = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {
                "detections": [
                    {
                        "detection_id": "http-persist",
                        "ra": pair["ra"],
                        "dec": pair["dec"],
                        "mjd": GAIA_EPOCH_MJD,
                        "sigma_arcsec": 0.05,
                    }
                ],
                "radius_arcsec": 0.5,
                "persist": True,
                "analysis_id": "analysis:http-persist",
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(data_again["persisted"], 2)
        conn = sqlite3.connect(self.db)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM catalog_match WHERE analysis_id=?",
                ("analysis:http-persist",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 2)

        # persist=true 缺 analysis_id → 400，不写库。
        code, bad = http_json(
            self.port,
            "POST",
            "/api/v1/catalog/match",
            {
                "detections": [
                    {"ra": pair["ra"], "dec": pair["dec"], "mjd": GAIA_EPOCH_MJD}
                ],
                "persist": True,
            },
        )
        self.assertEqual(code, 400)
        self.assertEqual(bad["code"], "BAD_REQUEST")
        self.assertIn("analysis_id", bad["message"])

        code, stats = http_json(self.port, "GET", "/api/v1/catalog/stats")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(stats["counts"]["matches"], 2)

    def test_http_docs_and_landing(self):
        code, raw, ctype = http_request(self.port, "GET", "/api/docs")
        self.assertEqual(code, 200)
        self.assertIn("text/markdown", ctype)
        doc = raw.decode("utf-8")
        self.assertIn("CC BY-NC 3.0 IGO", doc)
        self.assertIn("https://gea.esac.esa.int/tap-server/tap/sync", doc)
        self.assertIn("17,679", doc)
        self.assertIn("CATALOG_UNAVAILABLE", doc)

        code, raw, ctype = http_request(self.port, "GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("8799", raw.decode("utf-8"))


class StarCatalogUnavailableTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_missing_db_direct_and_server(self):
        missing = self.root / "missing.sqlite"
        with self.assertRaises(FileNotFoundError):
            StarCatalog(missing, readonly=True)
        with self.assertRaises(FileNotFoundError):
            make_server(missing)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = catalog_server.main(["--db", str(missing), "--port", "0"])
        self.assertEqual(rc, 2)
        self.assertIn("星表数据库不存在", err.getvalue())

    def test_bad_db_http_returns_catalog_unavailable(self):
        bad = self.root / "bad.sqlite"
        bad.write_bytes(b"this is not a sqlite database")
        with running_server(bad) as port:
            code, data = http_json(port, "GET", "/api/v1/health")
            self.assertEqual(code, 503)
            self.assertEqual(data["code"], "CATALOG_UNAVAILABLE")
            code, data = http_json(
                port, "GET", "/api/v1/catalog/cone?ra=269.45&dec=4.69"
            )
            self.assertEqual(code, 503)
            self.assertEqual(data["code"], "CATALOG_UNAVAILABLE")

    def test_empty_schema_http_returns_not_imported(self):
        db = self.root / "empty.sqlite"
        init_star_db(db)
        with running_server(db) as port:
            code, data = http_json(port, "GET", "/api/v1/health")
            self.assertEqual(code, 200)
            self.assertTrue(data["ok"])
            self.assertEqual(data["counts"]["sources"], 0)

            code, data = http_json(
                port, "GET", "/api/v1/catalog/cone?ra=269.45&dec=4.69"
            )
            self.assertEqual(code, 503)
            self.assertEqual(data["code"], "CATALOG_UNAVAILABLE")
            self.assertEqual(data["reason"], "catalog_not_imported")

            code, data = http_json(
                port,
                "POST",
                "/api/v1/catalog/match",
                {"detections": [{"ra": 269.45, "dec": 4.69}]},
            )
            self.assertEqual(code, 503)
            self.assertEqual(data["reason"], "catalog_not_imported")

    def test_db_deleted_after_start_returns_catalog_unavailable(self):
        db = self.root / "vanishing.sqlite"
        init_star_db(db)
        with running_server(db) as port:
            db.unlink()
            code, data = http_json(port, "GET", "/api/v1/health")
            self.assertEqual(code, 503)
            self.assertEqual(data["code"], "CATALOG_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
