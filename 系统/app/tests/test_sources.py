"""阶段 A：检测源点选（源表字段 / common WCS 映射 / 纯函数选择 / HTTP 无 500）。

不联网：WCS 用本地构造的小网格；HTTP 用 preset + synthetic 的离线模拟时序。
"""
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server  # noqa: E402
from core import data as data_layer  # noqa: E402
from core import pipeline  # noqa: E402
from core import simulator  # noqa: E402
from core.pipeline import Source  # noqa: E402
from core.wcs import WCS  # noqa: E402

SOURCE_KEYS = {
    "epoch_id",
    "frame",
    "x",
    "y",
    "ra",
    "dec",
    "flux",
    "flux_err",
    "snr",
    "saturated",
    "quality",
}
ALLOWED_QUALITY = {"SATURATED", "LOW_SNR", "EDGE", "OUT_OF_FRAME", "DUPLICATE"}


def _wcs(crpix1=16.5, crpix2=16.5, naxis=32, ra=10.0, dec=20.0):
    return WCS(
        crval1=ra,
        crval2=dec,
        crpix1=crpix1,
        crpix2=crpix2,
        cd=np.array([[-0.0017, 0.0], [0.0, 0.0017]]),
        naxis1=naxis,
        naxis2=naxis,
    )


class _FakeEpoch:
    def __init__(self, eid, wcs, shape=(32, 32)):
        self.id = eid
        self.wcs = wcs
        self.image = np.zeros(shape, float)


def _source(x=10.0, y=10.0, snr=50.0, flux=100.0, flux_err=2.0, saturated=False):
    return Source(
        x=float(x),
        y=float(y),
        flux=float(flux),
        flux_err=float(flux_err),
        snr=float(snr),
        sigma_x=0.2,
        sigma_y=0.2,
        saturated=bool(saturated),
    )


class SourceCatalogMappingTest(unittest.TestCase):
    """x/y 必须是 common WCS（第 1 期）像素，ra/dec 是本期的天球坐标。"""

    def test_shifted_epoch_maps_into_common_pixels(self):
        common = _wcs(crpix1=16.5)
        shifted = _wcs(crpix1=18.5)  # 本期图像相对公共网格右移 2 px
        epochs = [_FakeEpoch("e0", common), _FakeEpoch("e1", shifted)]
        catalogs = [[_source(x=10, y=10)], [_source(x=10, y=10)]]

        out = pipeline.source_catalog(epochs, catalogs, common)
        self.assertEqual(len(out), 2)
        rec0, rec1 = out
        self.assertEqual(rec0["frame"], 0)
        self.assertEqual(rec1["frame"], 1)
        self.assertEqual(rec1["epoch_id"], "e1")

        # 第一期就是公共网格本身：坐标不变
        self.assertAlmostEqual(rec0["x"], 10.0, places=6)
        self.assertAlmostEqual(rec0["y"], 10.0, places=6)
        # 右移 2 px 的历元里，同一天体在公共网格上应左移 2 px
        self.assertAlmostEqual(rec1["x"], 8.0, delta=0.01)
        self.assertAlmostEqual(rec1["y"], 10.0, delta=0.01)
        self.assertAlmostEqual(rec1["native_x"], 10.0, places=6)

        # 每个字段都不能省
        for rec in out:
            self.assertTrue(SOURCE_KEYS <= set(rec), SOURCE_KEYS - set(rec))
            self.assertEqual(rec["quality"], [])

        # ra/dec 与本期 WCS 的 pixel_to_world 一致
        ra1, dec1 = shifted.pixel_to_world(11.0, 11.0)
        self.assertAlmostEqual(rec1["ra"], float(ra1), places=6)
        self.assertAlmostEqual(rec1["dec"], float(dec1), places=6)
        # 用公共 WCS 反算回去必须落在 common x/y 上（1e-3 px）
        ra_c, dec_c = common.pixel_to_world(rec1["x"] + 1.0, rec1["y"] + 1.0)
        self.assertLess(math.hypot(float(ra_c) - rec1["ra"], float(dec_c) - rec1["dec"]), 1e-6)

    def test_outside_common_grid_is_flagged_not_dropped_or_faked(self):
        common = _wcs(crpix1=16.5)
        far = _wcs(crpix1=-20.0)  # 投影到公共网格外
        out = pipeline.source_catalog([_FakeEpoch("e0", far)], [[_source(x=10, y=10)]], common)
        self.assertEqual(len(out), 1)
        self.assertGreater(out[0]["x"], 31)
        self.assertIn("OUT_OF_FRAME", out[0]["quality"])

    def test_empty_catalog_and_bad_wcs_do_not_raise(self):
        common = _wcs()
        self.assertEqual(pipeline.source_catalog([], [], common), [])
        self.assertEqual(pipeline.source_catalog([_FakeEpoch("e0", common)], [[]], common), [])

        class _BrokenWCS:
            naxis1 = 32

            def pixel_to_world(self, x, y):
                raise RuntimeError("bad wcs")

        out = pipeline.source_catalog([_FakeEpoch("e0", _BrokenWCS())], [[_source()]], common)
        self.assertEqual(out, [])


class SourceQualityTest(unittest.TestCase):
    def test_good_source_has_empty_quality(self):
        self.assertEqual(pipeline.source_quality(_source(x=10, y=10, snr=20), (32, 32)), [])

    def test_edge_saturated_low_snr_are_flagged(self):
        self.assertIn("EDGE", pipeline.source_quality(_source(x=1, y=10), (32, 32)))
        self.assertIn("EDGE", pipeline.source_quality(_source(x=10, y=30.5), (32, 32)))
        self.assertIn("SATURATED", pipeline.source_quality(_source(saturated=True), (32, 32)))
        self.assertIn("LOW_SNR", pipeline.source_quality(_source(snr=4.9), (32, 32)))
        # 多个问题同时标出，不丢标记
        flags = pipeline.source_quality(_source(x=1, snr=1, saturated=True), (32, 32))
        self.assertEqual(set(flags), {"EDGE", "SATURATED", "LOW_SNR"})

    def test_nearby_duplicate_keeps_highest_snr(self):
        cat = [_source(x=10, y=10, snr=40), _source(x=10.4, y=10, snr=50), _source(x=20, y=20, snr=30)]
        kept = pipeline.dedupe_sources(cat)
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0].snr, 50.0)
        self.assertAlmostEqual(kept[1].x, 20.0)


class PipelineSourceCatalogTest(unittest.TestCase):
    """用离线模拟时序跑真实 pipeline，验证源表字段与 common WCS 映射。"""

    @classmethod
    def setUpClass(cls):
        target = data_layer.get_target("barnard")
        times = simulator.default_times(3, start_mjd=60930.0, span_days=130.0)
        cls.epochs, cls.truth = simulator.generate_epochs(
            target, times, fov_deg=0.2, n_stars=120, seed=5
        )
        cls.target = target
        cls.result = pipeline.run_pipeline(target, cls.epochs, truth=cls.truth, mode="synthetic")

    def test_sources_present_with_required_fields(self):
        sources = self.result.get("sources")
        self.assertIsInstance(sources, list)
        self.assertGreater(len(sources), 0)
        epoch_ids = {str(ep.id) for ep in self.epochs}
        frames = {rec["frame"] for rec in sources}
        self.assertTrue(frames <= set(range(len(self.epochs))))
        self.assertEqual(frames, set(range(len(self.epochs))))
        for rec in sources:
            self.assertTrue(SOURCE_KEYS <= set(rec), (SOURCE_KEYS - set(rec)))
            self.assertIn(rec["epoch_id"], epoch_ids)
            self.assertIsInstance(rec["quality"], list)
            self.assertTrue(set(rec["quality"]) <= ALLOWED_QUALITY, rec["quality"])
            self.assertIsInstance(rec["saturated"], bool)
            for key in ("x", "y", "ra", "dec", "flux", "flux_err", "snr"):
                self.assertTrue(math.isfinite(rec[key]), (key, rec[key]))

    def test_at_least_ten_selectable_sources_in_first_frame(self):
        good = [
            rec for rec in self.result["sources"] if rec["frame"] == 0 and not rec["quality"]
        ]
        self.assertGreaterEqual(len(good), 10)
        # 前端点选依赖 S/N 排序/辨识：至少 10 个源有非零 S/N
        self.assertTrue(all(rec["snr"] > 0 for rec in good))

    def test_common_wcs_mapping_roundtrips_for_every_source(self):
        common = self.epochs[0].wcs
        by_id = {str(ep.id): ep for ep in self.epochs}
        for rec in self.result["sources"]:
            ep = by_id[rec["epoch_id"]]
            # ra/dec 来自本期 WCS 的原生像素
            ra, dec = ep.wcs.pixel_to_world(rec["native_x"] + 1.0, rec["native_y"] + 1.0)
            self.assertLess(math.hypot(float(ra) - rec["ra"], float(dec) - rec["dec"]), 2e-6)
            # x/y 是公共网格像素：公共 WCS 反算应回到同一天球位置
            ra_c, dec_c = common.pixel_to_world(rec["x"] + 1.0, rec["y"] + 1.0)
            self.assertLess(math.hypot(float(ra_c) - rec["ra"], float(dec_c) - rec["dec"]), 2e-6)

    def test_no_duplicate_markers_per_frame(self):
        for frame in range(len(self.epochs)):
            recs = [rec for rec in self.result["sources"] if rec["frame"] == frame]
            for i, a in enumerate(recs):
                for b in recs[i + 1 :]:
                    self.assertGreaterEqual(
                        math.hypot(a["x"] - b["x"], a["y"] - b["y"]),
                        pipeline.SOURCE_DUPLICATE_RADIUS_PX - 1e-6,
                        (frame, a["x"], a["y"], b["x"], b["y"]),
                    )


class AnalyzeSourcesHTTPTest(unittest.TestCase):
    """真实 HTTP 路径：preset+synthetic（离线）→ 源表 → 以源坐标再分析。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        cls._analysis = None
        cls._status = None

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)
        server.STORE.clear()

    @classmethod
    def _post(cls, payload):
        raw = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            cls.base + "/api/analyze",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with cls.opener.open(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    @classmethod
    def analysis(cls):
        if cls._analysis is None:
            cls._status, cls._analysis = cls._post(
                {"target_id": "barnard", "mode": "synthetic", "fov": 0.2}
            )
        return cls._status, cls._analysis

    def test_analysis_returns_source_catalog_without_500(self):
        status, body = self.analysis()
        self.assertEqual(status, 200, body)
        self.assertIn("sources", body)
        self.assertIn("sources_meta", body)
        sources = body["sources"]
        self.assertGreater(len(sources), 0)
        for rec in sources:
            self.assertTrue(SOURCE_KEYS <= set(rec))
            self.assertTrue(set(rec["quality"]) <= ALLOWED_QUALITY)
        meta = body["sources_meta"]
        self.assertEqual(meta["n"], len(sources))
        self.assertEqual(len(meta["n_by_frame"]), len(body["epochs"]))
        self.assertEqual(meta["common_frame_epoch_id"], body["epochs"][0]["id"])
        self.assertGreaterEqual(meta["n_selectable"], 10)
        self.assertEqual(meta["quality_rule"]["snr_min"], pipeline.SOURCE_SNR_MIN)
        self.assertEqual(meta["quality_rule"]["edge_margin_px"], pipeline.SOURCE_EDGE_MARGIN_PX)

    def test_clicked_source_coordinates_analyze_without_500(self):
        status, body = self.analysis()
        self.assertEqual(status, 200, body)
        src = next(s for s in body["sources"] if s["frame"] == 0 and not s["quality"])
        status2, body2 = self._post(
            {"target_id": "custom", "ra": src["ra"], "dec": src["dec"], "fov": 0.2, "mode": "synthetic"}
        )
        self.assertEqual(status2, 200, body2)
        self.assertNotIn("error", body2)
        self.assertEqual(body2["target"]["id"], "custom")
        self.assertAlmostEqual(body2["target"]["ra"], src["ra"], places=6)
        self.assertAlmostEqual(body2["target"]["dec"], src["dec"], places=6)
        self.assertGreater(len(body2["sources"]), 0)

    def test_sample_mode_returns_empty_sources_not_fake(self):
        status, body = self._post({"target_id": "barnard", "mode": "sample", "fov": 0.25})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["sources"], [])
        self.assertEqual(body["sources_meta"]["n"], 0)
        self.assertIn("note", body["sources_meta"])


NODE = shutil.which("node")


class SourcePickJsTest(unittest.TestCase):
    """app/web/source_pick.js 的纯函数：common→屏幕映射、半径内最近源、质量过滤。"""

    @unittest.skipUnless(NODE, "node 不在 PATH，跳过 JS 纯函数测试")
    def test_pure_selection_helpers(self):
        js_path = os.path.join(APP, "web", "source_pick.js")
        script = r"""
const sp = require(process.argv[1]);
const sources = [
  {frame: 0, x: 49.5, y: 49.5, ra: 10.0, dec: 20.0, quality: [], snr: 10, epoch_id: "e0"},
  {frame: 0, x: 69.5, y: 49.5, ra: 10.1, dec: 20.0, quality: [], snr: 5, epoch_id: "e0"},
  {frame: 0, x: 29.5, y: 49.5, ra: 9.9, dec: 20.0, quality: ["SATURATED"], snr: 99, epoch_id: "e0"},
  {frame: 1, x: 49.5, y: 49.5, ra: 10.5, dec: 20.0, quality: [], snr: 7, epoch_id: "e1"}
];
function pick(frame, sx, sy, opts) {
  const hit = sp.sourceAt(sources, frame, sx, sy, 100, 1000, 12, opts);
  return hit ? {ra: hit.source.ra, quality: hit.source.quality, distance: hit.distance} : null;
}
console.log(JSON.stringify({
  center: pick(0, 500, 500),
  second: pick(0, 700, 500),
  outside: pick(0, 760, 500),
  flaggedSkipped: pick(0, 300, 500),
  flaggedHover: pick(0, 300, 500, {includeFlagged: true}),
  frame1: pick(1, 500, 500),
  screen: sp.commonToScreen(0, 0, 117, 936),
  roundtrip: sp.screenToCommon(500, 500, 100, 1000),
  sep: sp.angularSepDeg(10, 20, 10, 20.0001),
  nearest: sp.nearestSourceByRaDec(sources, 10, 20, 0.01),
  qualityText: sp.qualityText(sources[2]),
  selectable: sp.isSelectable(sources[0]),
  flaggedSelectable: sp.isSelectable(sources[2])
}));
"""
        proc = subprocess.run(
            [NODE, "-e", script, js_path], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertAlmostEqual(out["center"]["ra"], 10.0, places=6)
        self.assertEqual(out["center"]["distance"], 0)
        self.assertAlmostEqual(out["second"]["ra"], 10.1, places=6)
        self.assertIsNone(out["outside"])  # 超出 12 px 不选
        self.assertIsNone(out["flaggedSkipped"])  # 饱和源不可点选
        self.assertEqual(out["flaggedHover"]["quality"], ["SATURATED"])  # hover 仍可提示
        self.assertAlmostEqual(out["frame1"]["ra"], 10.5, places=6)  # 按当前帧过滤
        self.assertEqual(out["screen"], [4, 4])
        self.assertAlmostEqual(out["roundtrip"][0], 49.5, places=6)
        self.assertAlmostEqual(out["roundtrip"][1], 49.5, places=6)
        self.assertAlmostEqual(out["sep"], 1e-4, places=9)
        self.assertAlmostEqual(out["nearest"]["source"]["ra"], 10.0, places=6)
        self.assertEqual(out["qualityText"], "Saturated")
        self.assertTrue(out["selectable"])
        self.assertFalse(out["flaggedSelectable"])


if __name__ == "__main__":
    unittest.main()
