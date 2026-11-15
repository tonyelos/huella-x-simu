/* MoveScope · 阶段 A 源点选纯函数
 *
 * 只做数据换算与最近源选择，不碰 DOM：
 * - common WCS 像素 ↔ 画布屏幕像素
 * - 按帧筛选源、过滤不可点选的质量标记
 * - 点击/悬停时选最近源（阈值由调用方传入，单位：屏幕像素）
 * - 选中源与目标坐标的角距（用于 URL 复现时重新锁定）
 *
 * 浏览器里挂到 window.SourcePick；Node 里通过 module.exports 暴露，
 * 供 app/tests/test_sources.py 直接测试（无需浏览器）。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SourcePick = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const QUALITY_LABEL = {
    SATURATED: "Saturated",
    LOW_SNR: "Low S/N",
    EDGE: "Edge",
    OUT_OF_FRAME: "Out of frame",
    DUPLICATE: "Duplicate",
  };

  function qualityFlags(src) {
    return src && Array.isArray(src.quality) ? src.quality : [];
  }

  /** quality 为空才是有效源；带任何标记的源只能灰显，不能点选。 */
  function isSelectable(src) {
    return qualityFlags(src).length === 0;
  }

  function qualityText(src) {
    if (typeof window !== "undefined" && window.I18n && typeof window.I18n.qualityText === "function") {
      return window.I18n.qualityText(src);
    }
    const flags = qualityFlags(src);
    return flags.length ? flags.map((f) => QUALITY_LABEL[f] || f).join(" · ") : "Good quality";
  }

  function sourcesForFrame(sources, frame) {
    if (!Array.isArray(sources)) return [];
    return sources.filter((s) => Number(s.frame) === Number(frame));
  }

  /** common WCS 的 0-based 像素中心 → canvas 像素坐标。 */
  function commonToScreen(x, y, naxis, size) {
    const n = Number(naxis) || 1;
    return [((Number(x) + 0.5) / n) * Number(size), ((Number(y) + 0.5) / n) * Number(size)];
  }

  /** canvas 像素坐标 → common WCS 的 0-based 像素。 */
  function screenToCommon(sx, sy, naxis, size) {
    const n = Number(naxis) || 1;
    return [(Number(sx) / Number(size)) * n - 0.5, (Number(sy) / Number(size)) * n - 0.5];
  }

  /** items 已经是屏幕坐标（含 x/y）；返回最近的一个，超过 maxDist 返回 null。 */
  function pickNearestSource(items, x, y, maxDist) {
    let best = null;
    let bestD = Number(maxDist);
    for (const item of items || []) {
      const d = Math.hypot(Number(item.x) - x, Number(item.y) - y);
      if (d <= bestD) {
        best = item;
        bestD = d;
      }
    }
    return best ? { source: best, distance: bestD } : null;
  }

  /**
   * 在当前帧的源里选离 (sx, sy) 最近的一个。
   * opts.includeFlagged=true 时允许命中灰显源（用于 hover 提示），默认只选有效源。
   */
  function sourceAt(sources, frame, sx, sy, naxis, size, maxDist, opts) {
    const includeFlagged = !!(opts && opts.includeFlagged);
    const mapped = [];
    for (const s of sourcesForFrame(sources, frame)) {
      if (!includeFlagged && !isSelectable(s)) continue;
      const [px, py] = commonToScreen(s.x, s.y, naxis, size);
      mapped.push({ x: px, y: py, source: s });
    }
    const hit = pickNearestSource(mapped, sx, sy, maxDist);
    return hit ? { source: hit.source.source, distance: hit.distance } : null;
  }

  /** 球面角距（度）；小角度下稳定，RA 环绕安全。 */
  function angularSepDeg(ra1, dec1, ra2, dec2) {
    const d2r = Math.PI / 180;
    const sd = Math.sin(((Number(dec2) - Number(dec1)) * d2r) / 2);
    const sr = Math.sin(((Number(ra2) - Number(ra1)) * d2r) / 2);
    const a = sd * sd + Math.cos(Number(dec1) * d2r) * Math.cos(Number(dec2) * d2r) * sr * sr;
    if (!Number.isFinite(a)) return NaN;
    return (2 * Math.asin(Math.min(1, Math.sqrt(Math.max(a, 0))))) / d2r;
  }

  /** 按 RA/Dec 找最近的源（URL 带 lock=1 复现时用）；超出 maxSepDeg 返回 null。 */
  function nearestSourceByRaDec(sources, ra, dec, maxSepDeg) {
    let best = null;
    let bestSep = Number(maxSepDeg);
    for (const s of sources || []) {
      const sep = angularSepDeg(ra, dec, s.ra, s.dec);
      if (Number.isFinite(sep) && sep <= bestSep) {
        best = s;
        bestSep = sep;
      }
    }
    return best ? { source: best, sepDeg: bestSep } : null;
  }

  return {
    QUALITY_LABEL,
    qualityFlags,
    isSelectable,
    qualityText,
    sourcesForFrame,
    commonToScreen,
    screenToCommon,
    pickNearestSource,
    sourceAt,
    angularSepDeg,
    nearestSourceByRaDec,
  };
});
