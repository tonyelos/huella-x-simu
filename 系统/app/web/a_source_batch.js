/* A 源表批量识别：纯函数（无 DOM/网络），供 app.js 与 node 测试复用。 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ASourceBatch = factory();
})(typeof self !== "undefined" ? self : this, function () {
  function finite(v, fallback) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }

  function sourceKey(src) {
    return `${src && src.epoch_id != null ? src.epoch_id : ""}:${src && src.x != null ? src.x : ""}:${src && src.y != null ? src.y : ""}`;
  }

  function sigmaArcsec(src) {
    const sx = finite(src && src.sigma_x, 0);
    const sy = finite(src && src.sigma_y, 0);
    return Math.max(0.3, Math.hypot(sx, sy) * 6.15);
  }

  function sourceMjd(src, epochs) {
    const frame = Number(src && src.frame);
    const ep = Array.isArray(epochs) && Number.isInteger(frame) ? epochs[frame] : null;
    if (ep && Number.isFinite(Number(ep.mjd))) return Number(ep.mjd);
    if (src && Number.isFinite(Number(src.mjd))) return Number(src.mjd);
    return null;
  }

  function buildBatchSources(sources, epochs) {
    return (sources || []).map((src) => ({
      source_id: sourceKey(src),
      ra: Number(src.ra),
      dec: Number(src.dec),
      mjd: sourceMjd(src, epochs),
      sigma_arcsec: sigmaArcsec(src),
    }));
  }

  function chunks(items, size = 200) {
    const n = Math.max(1, Math.floor(Number(size) || 200));
    const out = [];
    for (let i = 0; i < (items || []).length; i += n) out.push(items.slice(i, i + n));
    return out;
  }

  function mergeCounts(into, add) {
    const out = { ...(into || {}) };
    for (const [k, v] of Object.entries(add || {})) out[k] = (out[k] || 0) + Number(v || 0);
    return out;
  }

  function badgeStatus(result) {
    if (!result) return "";
    if (result.status === "uncatalogued_candidate") return "no_match";
    if (result.status === "known_star") return "known";
    if (result.status === "ambiguous") return "ambiguous";
    if (result.status === "catalog_unavailable") return "unavailable";
    if (result.status === "insufficient_data") return "insufficient";
    return "";
  }

  function badgeLabel(status) {
    return { known: "known", ambiguous: "ambiguous", no_match: "no_match", insufficient: "insufficient", unavailable: "unavailable" }[status] || "";
  }

  return { sourceKey, sigmaArcsec, sourceMjd, buildBatchSources, chunks, mergeCounts, badgeStatus, badgeLabel };
});
