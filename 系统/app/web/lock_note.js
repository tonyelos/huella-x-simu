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
    known_star: "Gaia DR3 已知",
    ambiguous: "多候选 ambiguous",
    uncatalogued_candidate: "未收录候选（未经确认）",
    catalog_unavailable: "星表不可用",
    insufficient_data: "数据不足",
  };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function labelForStatus(status) {
    return STATUS_LABEL[status] || "星表不可用";
  }

  function sourceNote(src, ident) {
    const ra = Number(src && src.ra);
    const dec = Number(src && src.dec);
    const status = ident && ident.status ? String(ident.status) : "catalog_unavailable";
    const label = (ident && ident.label_zh) || labelForStatus(status);
    const kind = src && src.c_source ? "C 源" : "源";
    const raText = Number.isFinite(ra) ? ra.toFixed(6) : "—";
    const decText = Number.isFinite(dec) ? dec.toFixed(6) : "—";
    return `已锁定 ${kind} RA <b>${raText}°</b> · Dec <b>${decText}°</b> · ${esc(label)}（${esc(status)}）`;
  }

  return { STATUS_LABEL, labelForStatus, sourceNote };
});
