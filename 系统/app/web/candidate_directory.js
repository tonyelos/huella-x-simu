/* 统一候选目录：A/C 源行 + track 行；纯函数，不展开 track 节点。 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CandidateDirectory = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const COLUMNS = [
    "origin", "source_id", "epoch_id", "frame", "mjd", "ra", "dec",
    "status", "level", "model", "n_epochs", "catalog", "catalog_id",
    "separation_arcsec", "origins", "resolution", "raw_statuses", "caveats", "quality",
  ];

  function num(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function round6(v) {
    const n = num(v);
    return n == null ? "" : String(Math.round(n * 1e6) / 1e6);
  }

  function keyOf(src, kind) {
    const ra = round6(src.ra);
    const dec = round6(src.dec);
    return `${src.mjd == null ? "" : Number(src.mjd)}|${ra}|${dec}`;
  }

  function keyOfRow(row) {
    return `${row.mjd == null ? "" : Number(row.mjd)}|${round6(row.ra)}|${round6(row.dec)}`;
  }

  function sourceId(src, kind) {
    return kind === "c" ? String(src.source_id || "") : `${src.epoch_id != null ? src.epoch_id : ""}:${src.x != null ? src.x : ""}:${src.y != null ? src.y : ""}`;
  }

  function canonical(result) {
    if (!result || !result.status) return "unknown";
    // B 原始 match_status 是 no_match 时，即使 identification 因 partial
    // 返回 insufficient_data，也必须在审计目录保留 no_match。
    if (result.match_status === "no_match") return "no_match";
    if (result.status === "known_star") return "known";
    if (result.status === "ambiguous") return "ambiguous";
    if (result.status === "uncatalogued_candidate") return "no_match";
    if (result.status === "insufficient_data") return "insufficient";
    if (result.status === "catalog_unavailable") return "unknown";
    return "unknown";
  }

  function statusLabel(status, result) {
    if (result && result.label_zh) return result.label_zh;
    return {
      known: "known", ambiguous: "ambiguous", no_match: "未收录候选",
      insufficient: "数据不足", unknown: "未识别", conflict: "状态冲突",
    }[status] || status;
  }

  function join(v) {
    return Array.isArray(v) ? v.filter(Boolean).join(";") : (v || "");
  }

  function sourceRow(src, kind, result, epochs) {
    const status = canonical(result);
    const frame = Number.isInteger(Number(src.frame)) ? Number(src.frame) : "";
    const ep = Number.isInteger(frame) && Array.isArray(epochs) ? epochs[frame] : null;
    const mjd = (ep && num(ep.mjd) != null) ? num(ep.mjd) : num(src.mjd);
    const catalog = result ? (result.catalog || result.release || result.catalog_release || "") : "";
    const catalogId = (status === "known" && result) ? (result.catalog_id || "") : "";
    return {
      origin: kind.toUpperCase(),
      source_id: sourceId(src, kind),
      epoch_id: src.epoch_id != null ? String(src.epoch_id) : "",
      frame,
      mjd,
      ra: num(src.ra),
      dec: num(src.dec),
      status,
      level: "",
      model: "",
      n_epochs: "",
      catalog: catalog || "",
      catalog_id: catalogId,
      separation_arcsec: result ? num(result.separation_arcsec) : null,
      origins: kind.toUpperCase(),
      resolution: "single",
      raw_statuses: status,
      caveats: result ? join(result.caveats) : "no batch identification",
      quality: result ? join(result.quality) : "",
    };
  }

  function mergeSourceRows(base, extra) {
    const rawStatuses = [...new Set(`${base.raw_statuses || base.status};${extra.raw_statuses || extra.status}`.split(";").filter(Boolean))];
    const origins = [...new Set(`${base.origins};${extra.origins}`.split(";").filter(Boolean))].join(";");
    const bothKnown = base.status === "known" && extra.status === "known";
    const differentKnownId = bothKnown && base.catalog_id && extra.catalog_id && base.catalog_id !== extra.catalog_id;
    let status;
    let resolution;
    if (differentKnownId) {
      status = "conflict";
      resolution = "conflict_known_catalog_ids";
    } else if (rawStatuses.includes("ambiguous")) {
      status = "ambiguous";
      resolution = rawStatuses.includes("known") ? "ambiguous_over_known" : "priority_ambiguous";
    } else if (rawStatuses.includes("known")) {
      status = "known";
      resolution = "priority_known";
    } else if (rawStatuses.includes("insufficient")) {
      status = "insufficient";
      resolution = "priority_insufficient";
    } else if (rawStatuses.includes("no_match")) {
      status = "no_match";
      resolution = "priority_no_match";
    } else {
      status = "unknown";
      resolution = "priority_unknown";
    }
    const keepCatalog = ["known", "ambiguous"].includes(status) && !differentKnownId;
    return {
      ...base,
      origins,
      status,
      resolution,
      raw_statuses: rawStatuses.join(";"),
      catalog_id: differentKnownId ? "" : (keepCatalog ? (base.catalog_id || extra.catalog_id || "") : ""),
      catalog: differentKnownId ? "" : (keepCatalog ? (base.catalog || extra.catalog || "") : ""),
      caveats: `resolution=${resolution}; raw_statuses=${rawStatuses.join(";")}; ${base.caveats || ""}; ${extra.caveats || ""}`,
      quality: [base.quality, extra.quality].filter(Boolean).join(";"),
    };
  }

  function trackRow(track) {
    return {
      origin: "track",
      source_id: String(track.track_id || ""),
      epoch_id: "",
      frame: "",
      mjd: "",
      ra: num(track.ra),
      dec: num(track.dec),
      status: String(track.level || ""),
      level: String(track.level || ""),
      model: String(track.model || ""),
      n_epochs: track.n_epochs != null ? track.n_epochs : "",
      catalog: String(track.catalog || ""),
      catalog_id: track.catalog_id || "",
      separation_arcsec: null,
      origins: "track",
      caveats: join(track.caveats),
      quality: "",
    };
  }

  function build({ aSources = [], aById = {}, cSources = [], cById = {}, tracks = [], epochs = [] } = {}) {
    const merged = new Map();
    const add = (src, kind, result) => {
      const row = sourceRow(src, kind, result, epochs);
      const key = keyOfRow(row);
      if (merged.has(key)) merged.set(key, mergeSourceRows(merged.get(key), row));
      else merged.set(key, row);
    };
    for (const src of aSources) add(src, "a", aById[sourceId(src, "a")]);
    for (const src of cSources) add(src, "c", cById[String(src.source_id || "")]);
    const sources = [...merged.values()];
    const trackRows = (tracks || []).map(trackRow);
    const counts = {
      rows: sources.length + trackRows.length,
      unique_sources: sources.length,
      tracks: trackRows.length,
      known: 0, ambiguous: 0, no_match: 0, insufficient: 0, unknown: 0, conflict: 0,
    };
    for (const row of sources) {
      if (counts[row.status] != null) counts[row.status] += 1;
      else counts.unknown += 1;
    }
    return { counts, rows: [...sources, ...trackRows] };
  }

  function cell(v) {
    if (v == null) return "";
    const s = String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function toCsv(rows) {
    const lines = [COLUMNS.join(",")];
    for (const row of rows || []) lines.push(COLUMNS.map((k) => cell(row[k])).join(","));
    return "\ufeff" + lines.join("\n") + "\n";
  }

  function toJson(directory) {
    return JSON.stringify(directory, null, 2);
  }

  function downloadName(ext = "csv") {
    return `movescope_candidate_directory.${ext}`;
  }

  return { COLUMNS, build, toCsv, toJson, downloadName, keyOf, keyOfRow, sourceId };
});
