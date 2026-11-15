/* MoveScope · 锁定源状态文案（纯函数，可 Node 测试）
 *
 * 只负责把 identification 转成 #sourceNote 文案：
 * - C 源锁定后显示 RA/Dec + label_zh/status；
 * - 失败/超时/无结果一律按 catalog_unavailable；
 * - 不出现“正在查询”“未匹配 = 新天体”等误导性文案。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LockNote = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STATUS_LABEL = {
    known_star: "Known in catalog",
    ambiguous: "Multiple candidates (ambiguous)",
    uncatalogued_candidate: "Uncatalogued candidate (unconfirmed)",
    catalog_unavailable: "Catalog unavailable",
    insufficient_data: "Insufficient data",
  };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function labelForStatus(status) {
    if (typeof window !== "undefined" && window.I18n) {
      const localized = window.I18n.catalogLabel(status || "catalog_unavailable");
      if (localized) return localized;
    }
    return STATUS_LABEL[status] || "Catalog unavailable";
  }

  function sourceNote(src, ident) {
    const ra = Number(src && src.ra);
    const dec = Number(src && src.dec);
    const status = ident && ident.status ? String(ident.status) : "catalog_unavailable";
    const label = labelForStatus(status);
    const raText = Number.isFinite(ra) ? ra.toFixed(6) : "—";
    const decText = Number.isFinite(dec) ? dec.toFixed(6) : "—";
    if (typeof window !== "undefined" && window.I18n && src && src.c_source) {
      return window.I18n.t("note.lockCStatus", { ra: raText, dec: decText, label: esc(label), status: esc(status) });
    }
    return `Locked source RA <b>${raText}°</b> · Dec <b>${decText}°</b> · ${esc(label)} (${esc(status)})`;
  }

  return { STATUS_LABEL, labelForStatus, sourceNote };
});
