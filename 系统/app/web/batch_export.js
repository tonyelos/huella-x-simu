/* A/C 批量识别审计导出：纯函数（CSV/JSON），不访问 DOM/网络。 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.BatchExport = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const COLUMNS = [
    "source_id", "epoch_id", "frame", "ra", "dec", "mjd",
    "status", "label_zh", "catalog_id", "release", "separation_arcsec",
    "ruwe", "n_candidates", "jmag", "hmag", "kmag", "w1", "w2",
    "reason", "quality", "caveats",
  ];
  const NO_MATCH_LABEL = "未收录候选（未经确认）";

  function finite(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function sourceKey(src, kind) {
    if (kind === "c") return String(src && src.source_id != null ? src.source_id : "");
    return `${src && src.epoch_id != null ? src.epoch_id : ""}:${src && src.x != null ? src.x : ""}:${src && src.y != null ? src.y : ""}`;
  }

  function photo(result) {
    return (result && result.photometry) || {};
  }

  function buildRows(sources, byId, kind, epochs) {
    const map = byId || {};
    const list = Array.isArray(sources) ? sources : [];
    const eps = Array.isArray(epochs) ? epochs : [];
    return list.map((src) => {
      const sid = sourceKey(src, kind);
      const result = map[sid] || {};
      const status = result.status || "";
      const ambiguous = status === "ambiguous" || Number(result.n_candidates || (result.candidates || []).length) > 1;
      const frame = Number.isInteger(Number(src.frame)) ? Number(src.frame) : "";
      const ep = Number.isInteger(frame) && eps[frame] ? eps[frame] : null;
      const mjd = (ep && finite(ep.mjd)) ?? finite(src.mjd) ?? finite(result.input && result.input.mjd);
      const ph = photo(result);
      const candidates = Array.isArray(result.candidates) ? result.candidates : [];
      const nCand = result.n_candidates != null ? result.n_candidates : candidates.length;
      const label = status === "uncatalogued_candidate" && !result.label_zh ? NO_MATCH_LABEL : (result.label_zh || "");
      const catalogId = ambiguous || status === "uncatalogued_candidate" ? null : (result.catalog_id || null);
      const release = result.release || result.catalog_release || (candidates[0] && candidates[0].release) || "";
      return {
        source_id: sid,
        epoch_id: src.epoch_id != null ? String(src.epoch_id) : "",
        frame,
        ra: finite(src.ra),
        dec: finite(src.dec),
        mjd,
        status,
        label_zh: label,
        catalog_id: catalogId,
        release,
        separation_arcsec: finite(result.separation_arcsec),
        ruwe: ambiguous ? null : finite(result.ruwe),
        n_candidates: nCand,
        jmag: finite(ph.jmag),
        hmag: finite(ph.hmag),
        kmag: finite(ph.kmag),
        w1: finite(ph.w1),
        w2: finite(ph.w2),
        reason: result.reason || "",
        quality: (result.quality || []).join(";"),
        caveats: (result.caveats || []).join(";"),
      };
    });
  }

  function cell(value) {
    if (value == null) return "";
    const s = String(value);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function toCsv(rows) {
    const lines = [COLUMNS.join(",")];
    for (const row of rows || []) lines.push(COLUMNS.map((k) => cell(row[k])).join(","));
    // UTF-8 BOM: Excel 打开中文 label/caveat 不乱码。
    return "\ufeff" + lines.join("\n") + "\n";
  }

  function toJson(rows) {
    return JSON.stringify(rows || [], null, 2);
  }

  function utcStamp(now) {
    const d = now instanceof Date ? now : new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}T${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}Z`;
  }

  function downloadName(kind, target, ext = "csv", now = null) {
    const k = String(kind || "A").toUpperCase();
    const t = String(target || "target").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "target";
    return `movescope_${k}_batch_${t}_${utcStamp(now)}.${ext}`;
  }

  return { COLUMNS, NO_MATCH_LABEL, buildRows, toCsv, toJson, downloadName, sourceKey };
});
