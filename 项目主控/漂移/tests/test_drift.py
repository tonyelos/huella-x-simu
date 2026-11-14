"""T6 漂移检查器的 fixture 自测。

运行：
    cd 项目主控/漂移
    python3 -m unittest discover -s tests -v

每个用例在临时目录生成一套最小但结构完整的 app/DB/view 数据与源码片段，
再以子进程方式运行 drift.py，断言退出码与报告内容。测试不会触碰真实仓库数据。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

DRIFT = Path(__file__).resolve().parents[1] / "drift.py"


def iso_from_mjd(mjd: float) -> str:
    dt = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=float(mjd))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


TRUTH = {
    "mu_mas_yr": 100.0,
    "pa_deg": 90.0,
    "parallax_mas": 10.0,
    "pmra_mas_yr": 100.0,
    "pmdec_mas_yr": 0.0,
    "period_d": None,
    "amp_mag": None,
}

TARGETS = [
    {
        "id": "barnard",
        "name": "Barnard's Star",
        "name_zh": "巴纳德星",
        "official": True,
        "ra": 10.0,
        "dec": 20.0,
        "type": "M4V",
        "expected": "L",
        "coord_unc_arcsec": 3.0,
        "truth": TRUTH,
        "note": "fixture",
    },
    {
        "id": "campo_a",
        "name": "Field A",
        "name_zh": "场 A",
        "official": False,
        "ra": 11.0,
        "dec": 21.0,
        "type": "field",
        "expected": "S",
        "coord_unc_arcsec": 3.0,
        "truth": {key: None for key in TRUTH},
        "note": "fixture",
    },
]

EPOCHS = {
    "barnard": [
        {
            "release": "dr1",
            "week": "2025W01_1A",
            "version": "v1",
            "field": 1,
            "detector": 1,
            "key": "dr1/level2/2025W01_1A/v1/1/a.fits",
            "obsid": "2025W01_1A_0001_1",
            "mjd": 60001.1,
            "date_obs": iso_from_mjd(60001.1),
            "sep_deg": 0.1,
            "target_id": "barnard",
            "slice_url_002": "https://example/a?size=0.05",
        },
        {
            "release": "dr1",
            "week": "2025W01_1A",
            "version": "v1",
            "field": 1,
            "detector": 2,
            "key": "dr1/level2/2025W01_1A/v1/2/b.fits",
            "obsid": "2025W01_1A_0001_1",
            "mjd": 60001.1,
            "date_obs": iso_from_mjd(60001.1),
            "sep_deg": 0.2,
            "target_id": "barnard",
            "slice_url_002": "https://example/b?size=0.05",
        },
    ],
    "campo_a": [
        {
            "release": "dr1",
            "week": "2025W02_1A",
            "version": "v1",
            "field": 2,
            "detector": 1,
            "key": "dr1/level2/2025W02_1A/v1/1/c.fits",
            "obsid": "2025W02_1A_0002_1",
            "mjd": 60002.2,
            "date_obs": iso_from_mjd(60002.2),
            "sep_deg": 0.3,
            "target_id": "campo_a",
            "slice_url_002": "https://example/c?size=0.05",
        }
    ],
}

FIELDS = [
    ["2025W01_1A_0001_1", 1, 10.12345, 20.12345, 60001.1, 1.0, 2.0, 3.0, 4.0, 113.6],
    ["2025W01_1A_0001_1", 2, 10.12346, 20.12346, 60001.1, 1.1, 2.1, 3.1, 4.1, 113.6],
    ["2025W02_1A_0002_1", 1, 11.12345, 21.12345, 60002.2, 1.2, 2.2, 3.2, 4.2, 113.6],
    ["2025W02_1A_0002_1", 2, 11.12346, 21.12346, 60002.2, 1.3, 2.3, 3.3, 4.3, 113.6],
]

EXPECT = {
    "targets_total": 2,
    "targets_official": 1,
    "epochs_total": 3,
    "fields_total": 4,
    "exposures_total": 2,
    "db_weeks_total": 2,
    "local_weeks_total": 2,
    "headers_total": 3,
    "cutouts_total": 0,
    "coverage_radius_deg": 2.47,
    "frame_half_width_deg": 1.745,
    "dedupe_tol_days": 0.02,
    "l3_min_epochs": 6,
    "l3_min_baseline_years": 0.6,
    "coverage_c3_min_days": 180.0,
    "app_port": 8790,
    "db_port": 8797,
    "view_port": 8830,
    "schema_version": 6,
    "api_schema_version": "1.0",
    "app_tests": 0,
    "db_tests": 0,
    "docs": [
        "README.md",
        "系统/数据库工具/README.md",
        "系统/数据库工具/benchmarks/README.md",
        "系统/数据库工具/docs/INTEGRATION.md",
        "系统/视图工具/README.md",
        "系统/app/data/header_cache/README.md",
    ],
}


class DriftFixture:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="t6-drift-test-"))

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, rel: str, payload: object) -> Path:
        return self.write(rel, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def build(self) -> None:
        app_targets = {"schema": "movescope/targets/v1", "targets": TARGETS}
        self.write_json("系统/app/data/targets.json", app_targets)
        view_targets = {
            "schema": "movescope/targets/v1",
            "source": "app/data/targets.json",
            "source_sha256": __import__("hashlib").sha256(
                (self.root / "系统/app/data/targets.json").read_bytes()
            ).hexdigest(),
            "count": len(TARGETS),
            "targets": TARGETS,
        }
        self.write_json("系统/视图工具/data/targets.json", view_targets)
        self.write_json(
            "系统/app/data/epoch_catalog.json",
            {"generated_by": "fixture", "epochs": EPOCHS},
        )
        export_epochs = {
            "generated_by": "fixture",
            "generated_at": "2026-01-01T00:00:00Z",
            "epochs": EPOCHS,
        }
        self.write_json("系统/数据库工具/var/exports/epoch_catalog.json", export_epochs)
        self.write_json(
            "系统/视图工具/data/fields.json",
            {
                "generated_by": "fixture",
                "source": "db:fixture",
                "count": len(FIELDS),
                "exposures": 2,
                "keys": ["obsid", "detector", "ra", "dec", "mjd", "lat", "lon", "elon", "elat", "xposure"],
                "fields": FIELDS,
            },
        )
        self.write_json(
            "系统/数据库工具/var/exports/fields.json",
            {
                "generated_by": "fixture",
                "source": "db:fixture",
                "count": len(FIELDS),
                "keys": ["obsid", "detector", "ra", "dec", "mjd", "lat", "lon", "elon", "elat", "xposure"],
                "fields": FIELDS,
            },
        )
        self.write_json(
            "系统/app/data/header_manifest.json",
            {"generated_by": "fixture", "summary": {}, "entries": [{"key": e["key"]} for items in EPOCHS.values() for e in items]},
        )
        self._build_db()
        self._build_sources()
        self._build_docs()
        self.write_json("expect.json", EXPECT)

    def _build_db(self) -> None:
        db_path = self.root / "系统/数据库工具/var/catalog.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE targets (
                id TEXT PRIMARY KEY, name TEXT, name_zh TEXT, official INTEGER,
                ra REAL, dec REAL, type TEXT, expected TEXT, truth_json TEXT,
                note TEXT, aliases TEXT, updated_at TEXT, extra_json TEXT
            );
            CREATE TABLE frames (
                frame_id INTEGER PRIMARY KEY, obs_id TEXT, detector INTEGER,
                ra REAL, dec REAL, mjd REAL, sgt_lat REAL, sgt_lon REAL,
                elon REAL, elat REAL, xposure REAL, week TEXT, release TEXT,
                date_obs TEXT, header_sha256 TEXT
            );
            CREATE TABLE target_epochs (
                id INTEGER PRIMARY KEY, target_id TEXT, frame_id INTEGER,
                release TEXT, week TEXT, obs_id TEXT, detector INTEGER,
                mjd REAL, date_obs TEXT, sep_deg REAL, rank INTEGER,
                fits_key TEXT, slice_url TEXT, source TEXT, created_at TEXT
            );
            CREATE TABLE fields_snapshot (
                seq INTEGER PRIMARY KEY, obsid TEXT, detector INTEGER,
                ra REAL, dec REAL, mjd REAL, lat REAL, lon REAL,
                elon REAL, elat REAL, xposure REAL
            );
            CREATE TABLE observations (obs_id TEXT PRIMARY KEY, week TEXT, mjd REAL);
            CREATE TABLE weeks (release TEXT, week TEXT, PRIMARY KEY (release, week));
            CREATE TABLE cutouts (cutout_id TEXT PRIMARY KEY);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        for idx, target in enumerate(TARGETS):
            conn.execute(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    target["id"],
                    target["name"],
                    target["name_zh"],
                    1 if target["official"] else 0,
                    target["ra"],
                    target["dec"],
                    target["type"],
                    target["expected"],
                    json.dumps(target["truth"], ensure_ascii=False),
                    target["note"],
                    "",
                    "2026-01-01T00:00:00Z",
                    json.dumps({"coord_unc_arcsec": target["coord_unc_arcsec"]}, ensure_ascii=False),
                ),
            )
        frame_ids: dict[tuple[str, int], int] = {}
        for frame_id, row in enumerate(FIELDS, 1):
            frame_ids[(row[0], int(row[1]))] = frame_id
            conn.execute(
                "INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    frame_id,
                    row[0],
                    int(row[1]),
                    row[2] + 0.000001,
                    row[3] + 0.000001,
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    "2025W01_1A" if row[0].startswith("2025W01") else "2025W02_1A",
                    "dr1",
                    iso_from_mjd(row[4]),
                    None,
                ),
            )
        epoch_id = 1
        for target_id, items in EPOCHS.items():
            for item in items:
                conn.execute(
                    "INSERT INTO target_epochs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        epoch_id,
                        target_id,
                        frame_ids[(item["obsid"], int(item["detector"]))],
                        item["release"],
                        item["week"],
                        item["obsid"],
                        int(item["detector"]),
                        item["mjd"],
                        item["date_obs"],
                        item["sep_deg"],
                        0,
                        item["key"],
                        item["slice_url_002"],
                        "fixture",
                        "2026-01-01T00:00:00Z",
                    ),
                )
                epoch_id += 1
        for seq, row in enumerate(FIELDS):
            conn.execute(
                "INSERT INTO fields_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (seq, *row),
            )
        for obsid, week in (("2025W01_1A_0001_1", "2025W01_1A"), ("2025W02_1A_0002_1", "2025W02_1A")):
            conn.execute("INSERT INTO observations VALUES (?,?,?)", (obsid, week, 60001.1))
        for week in ("2025W01_1A", "2025W02_1A"):
            conn.execute("INSERT INTO weeks VALUES (?,?)", ("dr1", week))
        conn.execute("INSERT INTO meta VALUES ('schema_version', '6')")
        conn.execute("INSERT INTO meta VALUES ('tool_version', '1.1.0')")
        conn.commit()
        conn.close()

    def _build_sources(self) -> None:
        self.write(
            "系统/app/core/epoch_source.py",
            "def _dedupe_and_sort(epochs, tol_days: float = 0.02):\n"
            "    return epochs\n\n"
            "def coverage_for_epochs(epochs):\n"
            '    return {"coverage_level": "C0"}\n',
        )
        self.write(
            "系统/app/core/data.py",
            "def dedupe_epochs(eps, tol_days: float = 0.02):\n    return eps\n",
        )
        self.write(
            "系统/app/core/motion.py",
            "def analyze(n, baseline_years):\n"
            '    readiness = "L0"\n'
            "    if n >= 6 and baseline_years >= 0.6:\n"
            '        readiness = "L3"\n'
            '    return {"readiness": readiness}\n',
        )
        self.write(
            "系统/app/server.py",
            'THEORY_BLOCK = {"readiness": {"L3": "≥6 期且跨 6 个月：可判加速/视差/周期"}}\n\n'
            "def main():\n"
            "    import argparse\n"
            "    ap = argparse.ArgumentParser()\n"
            '    ap.add_argument("--port", type=int, default=8790)\n',
        )
        self.write(
            "系统/数据库工具/db/config.py",
            "COVERAGE_RADIUS_DEG = 2.47\n"
            "FRAME_HALF_WIDTH_DEG = 1.745\n"
            "FRAME_RADIUS_DEG = FRAME_HALF_WIDTH_DEG\n"
            "SCHEMA_VERSION = 6\n"
            'API_SCHEMA_VERSION = "1.0"\n'
            'TOOL_VERSION = "1.1.0"\n'
            'READINESS_LABELS = {"L3": "≥6 期且跨 6 个月：可判加速/视差/周期"}\n'
            'COVERAGE_LABELS = {"C3": "≥6 期且基线 ≥180 天"}\n',
        )
        self.write(
            "系统/数据库工具/db/catalog.py",
            "def readiness_level(n_epochs, baseline_days):\n"
            '    if n_epochs <= 1:\n        return "L0"\n'
            '    if n_epochs == 2:\n        return "L1"\n'
            '    if n_epochs < 6:\n        return "L2"\n'
            '    return "L3" if baseline_days >= 180 else "L2"\n\n'
            "def coverage_level(n_epochs, baseline_days):\n"
            '    if n_epochs <= 1:\n        return "C0"\n'
            '    if n_epochs == 2:\n        return "C1"\n'
            '    if n_epochs < 6:\n        return "C2"\n'
            '    return "C3" if baseline_days >= 180 else "C2"\n\n'
            "def dedupe_epoch_refs(refs, *, tol: float = 0.02):\n"
            "    return refs\n\n"
            "class Catalog:\n"
            "    def coverage(self, ra, dec):\n"
            "        last = None\n"
            "        if last and abs(60000.0 - last) <= 0.02:\n"
            "            pass\n"
            '        return {"readiness": {"level": "L2", "deprecated": True, '
            '"note": "覆盖等级请用 coverage_level；analysis_readiness 由 MoveScope 管线给出。"}}\n',
        )
        self.write(
            "系统/数据库工具/db/server.py",
            "def _api_v1_coverage(self, q):\n"
            "    out = {}\n"
            '    out.pop("readiness", None)\n'
            "    return out\n\n"
            "def serve(port: int = 8797):\n"
            "    return port\n",
        )
        self.write(
            "系统/数据库工具/client.py",
            "def epochs_v1(ra, dec, fov=0.2, radius=2.47):\n    return {}\n\n"
            "def coverage_v1(ra, dec, radius=2.47):\n    return {}\n",
        )
        self.write(
            "系统/数据库工具/cli.py",
            "def main():\n"
            "    import argparse\n"
            "    sv = argparse.ArgumentParser()\n"
            '    sv.add_argument("--port", type=int, default=8797)\n',
        )
        self.write("系统/数据库工具/start.sh", '#!/usr/bin/env bash\nPORT="${1:-8797}"\n')
        self.write("系统/视图工具/start.sh", '#!/usr/bin/env bash\nPORT="${1:-8830}"\n')
        self.write(
            "项目主控/启动/lib.sh",
            'APP_PORT="${LAUNCH_APP_PORT:-8790}"\n'
            'DB_PORT="${LAUNCH_DB_PORT:-8797}"\n'
            'VIEW_PORT="${LAUNCH_VIEW_PORT:-8830}"\n',
        )
        self.write(
            "系统/视图工具/js/main.js",
            "const MOVESCOPE_ORIGIN = String(\n"
            "  window.MOVESCOPE_ORIGIN || 'http://127.0.0.1:8790'\n"
            ");\n"
            "const HIT_RADIUS_DEG = 2.47;\n",
        )
        self.write(
            "系统/视图工具/js/skymap.js",
            "const lim = 2.47;\nconst half = 3.49 / 2; // 足迹半宽\n",
        )
        self.write(
            "系统/视图工具/index.html",
            '<span>本地命中 <em>2.47°</em></span>\n',
        )

    def _build_docs(self) -> None:
        self.write("README.md", "# Fixture\n")
        self.write(
            "系统/数据库工具/README.md",
            "# Fixture DB\n\n当前库：4 帧 / 2 次曝光 / 3 条目标历元 / 2 个目标 / 2 个周 / schema v6。\n",
        )
        self.write(
            "系统/数据库工具/benchmarks/README.md",
            "| 文件 | 数据 |\n|---|---|\n| `report.json` | 生产库 4 帧（真实元数据） |\n",
        )
        self.write(
            "系统/数据库工具/docs/INTEGRATION.md",
            "# Integration\n\n当前库是 4 帧 / 3 条历元；本地索引 2 个观测周。\n",
        )
        self.write(
            "系统/视图工具/README.md",
            "# Fixture View\n\n把 4 帧 / 2 次曝光变成三维地球；本地 2 个观测周。\n\n"
            "`fields.json` 4 帧（生成物）。\n",
        )
        self.write(
            "系统/app/data/header_cache/README.md",
            "# Header cache\n\n当前 3 份，对应 DB `target_epochs` 的 3 条候选历元。\n",
        )

    def run(self, *, extra_args: list[str] | None = None, out_name: str = "out") -> subprocess.CompletedProcess[str]:
        out_dir = self.root / out_name
        args = [
            sys.executable,
            str(DRIFT),
            "--root",
            str(self.root),
            "--expect",
            str(self.root / "expect.json"),
            "--out-dir",
            str(out_dir),
        ]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(args, capture_output=True, text=True, timeout=60)

    def report(self, out_name: str = "out") -> dict:
        return json.loads((self.root / out_name / "report.json").read_text(encoding="utf-8"))


class DriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = DriftFixture()
        self.fixture.build()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_clean_fixture_exits_0(self) -> None:
        proc = self.fixture.run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = self.fixture.report()
        self.assertEqual(report["exit_code"], 0)
        self.assertFalse(report["summary"]["drift"])
        statuses = {check["id"]: check["status"] for check in report["checks"]}
        self.assertNotIn("FAIL", statuses.values())
        self.assertEqual(statuses["targets"], "PASS")
        self.assertEqual(statuses["epochs"], "PASS")
        self.assertEqual(statuses["fields"], "PASS")
        self.assertEqual(statuses["docs"], "PASS")

    def test_constants_accept_js_expression_and_window_origin(self) -> None:
        proc = self.fixture.run(out_name="js-shape")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = self.fixture.report("js-shape")
        constants = next(check for check in report["checks"] if check["id"] == "constants")
        self.assertEqual(constants["status"], "PASS")
        groups = {row["label"]: row for row in constants["stats"]["constants"]}
        half_values = [item["actual"] for item in groups["单片半宽"]["values"]]
        self.assertIn(1.745, half_values)
        origin_values = [
            item["actual"]
            for item in groups["MoveScope 端口"]["values"]
            if "MOVESCOPE_ORIGIN" in item["label"]
        ]
        self.assertEqual(origin_values, [8790])

    def test_missing_db_marks_db_checks_skip(self) -> None:
        proc = self.fixture.run(extra_args=["--db", str(self.fixture.root / "no-such.sqlite")], out_name="nodb")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = self.fixture.report("nodb")
        statuses = {check["id"]: check["status"] for check in report["checks"]}
        for check in ("targets", "epochs", "fields", "schema"):
            self.assertEqual(statuses[check], "SKIP", statuses)
        self.assertEqual(report["exit_code"], 0)

    def test_targets_drift_exits_1(self) -> None:
        view = json.loads((self.fixture.root / "系统/视图工具/data/targets.json").read_text(encoding="utf-8"))
        view["targets"][0]["ra"] = 99.0
        self.fixture.write_json("系统/视图工具/data/targets.json", view)
        proc = self.fixture.run(out_name="targets")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = self.fixture.report("targets")
        self.assertTrue(any(f["check"] == "targets" and f["severity"] == "error" for f in report["findings"]))

    def test_epochs_drift_exits_1(self) -> None:
        db_path = self.fixture.root / "系统/数据库工具/var/catalog.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM target_epochs WHERE id = 3")
        conn.commit()
        conn.close()
        proc = self.fixture.run(out_name="epochs")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = self.fixture.report("epochs")
        self.assertTrue(any(f["check"] == "epochs" and f["severity"] == "error" for f in report["findings"]))

    def test_fields_drift_exits_1(self) -> None:
        view = json.loads((self.fixture.root / "系统/视图工具/data/fields.json").read_text(encoding="utf-8"))
        view["fields"][0][2] = 99.0
        self.fixture.write_json("系统/视图工具/data/fields.json", view)
        proc = self.fixture.run(out_name="fields")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = self.fixture.report("fields")
        self.assertTrue(any(f["check"] == "fields" and f["severity"] == "error" for f in report["findings"]))

    def test_constants_drift_exits_1(self) -> None:
        self.fixture.write(
            "系统/数据库工具/db/config.py",
            (self.fixture.root / "系统/数据库工具/db/config.py").read_text(encoding="utf-8").replace(
                "COVERAGE_RADIUS_DEG = 2.47", "COVERAGE_RADIUS_DEG = 1.745"
            ),
        )
        proc = self.fixture.run(out_name="constants")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = self.fixture.report("constants")
        self.assertTrue(any(f["check"] == "constants" and f["severity"] == "error" for f in report["findings"]))

    def test_readiness_misuse_exits_1(self) -> None:
        self.fixture.write(
            "系统/app/core/epoch_source.py",
            "def _dedupe_and_sort(epochs, tol_days: float = 0.02):\n"
            "    return epochs\n\n"
            "def coverage_for_epochs(epochs):\n"
            '    return {"coverage_level": "C0", "readiness": "L0"}\n',
        )
        proc = self.fixture.run(out_name="readiness")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = self.fixture.report("readiness")
        self.assertTrue(any(f["check"] == "readiness" and f["severity"] == "error" for f in report["findings"]))

    def test_missing_core_exits_2(self) -> None:
        (self.fixture.root / "系统/app/data/targets.json").unlink()
        proc = self.fixture.run(out_name="missing")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        report = self.fixture.report("missing")
        self.assertEqual(report["exit_code"], 2)
        self.assertTrue(report["environment"]["missing_core"])

    def test_stale_doc_number_exits_1(self) -> None:
        self.fixture.write(
            "系统/视图工具/README.md",
            "# Fixture View\n\n把 4 帧 / 2 次曝光变成三维地球；本地 2 个观测周。\n\n"
            "`fields.json` 4 帧（生成物）。\n\n历史备注：5,857 帧。\n",
        )
        # 带“历史”标记的行应被忽略；再加一条不带标记的旧数字。
        self.fixture.write(
            "系统/视图工具/README.md",
            (self.fixture.root / "系统/视图工具/README.md").read_text(encoding="utf-8")
            + "\n当前 fields.json 5,857 帧。\n",
        )
        proc = self.fixture.run(out_name="docs")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = self.fixture.report("docs")
        docs_errors = [f for f in report["findings"] if f["check"] == "docs" and f["severity"] == "error"]
        self.assertEqual(len(docs_errors), 1, docs_errors)
        self.assertIn("5857", docs_errors[0]["message"])


if __name__ == "__main__":
    unittest.main()
