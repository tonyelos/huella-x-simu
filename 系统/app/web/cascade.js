/* MoveScope · 级联/残差页（无构建，原生 JS）
 *
 * 只做展示与 /api/cascade、/api/identify 调用：
 * - 不把 residual 说成新天体；
 * - 不在前端做星表匹配/运动判定；
 * - 星表不可用时明确显示，不写成“未匹配”。
 */
window.I18n.init();
const tr = (key, params) => window.I18n.t(key, params);
const $ = (sel) => document.querySelector(sel);
const state = {
  targets: [],
  data: null,
  selected: null,
  byId: new Map(),
};

function apiError(e, fallback) {
  const err = new Error((e && (e.message || e.code)) || fallback || tr("apiError.generic"));
  if (e && e.code) err.code = e.code;
  return err;
}

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function targetDisplayName(t) {
  if (!t) return "";
  const fallback = { m101: "target.m101", campo_a: "target.campo_a" }[t.id];
  if (fallback && window.I18n.hasKey(fallback)) return tr(fallback);
  return /[\u4e00-\u9fff]/.test(String(t.name || "")) ? (fallback ? tr(fallback) : String(t.name || t.id || "")) : (t.name || t.id || "");
}

function fmtNum(v, digits = 5) {
  return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits);
}

function fmtSnr(v) {
  return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(1);
}

function readTimeWindow() {
  const t0 = $("#t0").value.trim();
  const t1 = $("#t1").value.trim();
  if (!t0 && !t1) return { ok: true, t0: null, t1: null };
  if (!t0 || !t1) return { ok: false, error: tr("note.readTimeBoth") };
  const a = Number(t0), b = Number(t1);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return { ok: false, error: tr("note.readTimeNumber") };
  if (a > b) return { ok: false, error: tr("note.readTimeOrder") };
  return { ok: true, t0: a, t1: b };
}

function timeFilterText(tf) {
  if (!tf || !tf.filtered) return tf && tf.requested != null ? tr("time.allPeriodsRequested", { n: tf.requested }) : tr("time.allPeriods");
  return tr("time.window", { t0: Number(tf.t0).toFixed(3), t1: Number(tf.t1).toFixed(3), kept: tf.kept, requested: tf.requested });
}

function updateViewLink(sel, fov, win) {
  const link = $("#viewLink");
  if (!link) return;
  const u = new URL("http://127.0.0.1:8830/");
  u.searchParams.set("from", "analysis");
  if (sel && sel.target_id) u.searchParams.set("target_id", sel.target_id);
  else if (sel && Number.isFinite(sel.ra) && Number.isFinite(sel.dec)) {
    u.searchParams.set("ra", String(sel.ra));
    u.searchParams.set("dec", String(sel.dec));
  }
  if (Number.isFinite(fov)) u.searchParams.set("fov", String(fov));
  if (win && win.t0 != null) u.searchParams.set("t0", String(win.t0));
  if (win && win.t1 != null) u.searchParams.set("t1", String(win.t1));
  link.href = u.toString();
  link.classList.remove("hidden");
}

function note(html, cls = "") {
  const el = $("#cascadeNote");
  el.innerHTML = html;
  el.className = "note " + cls;
}

function qualityText(src) {
  return window.I18n.qualityText(src);
}

function sourceTable(rows, stageLabel) {
  if (!rows || !rows.length) return `<div class="cascade-empty">${esc(tr("cascade.layer.none"))}</div>`;
  const shown = rows.slice(0, 80);
  const trs = shown.map((s) => {
    const sid = esc(s.source_id || "");
    return `<tr class="source-row" data-source-id="${sid}">
      <td>${esc(stageLabel)}</td>
      <td>${fmtSnr(s.snr)}</td>
      <td>${fmtNum(s.ra)}</td>
      <td>${fmtNum(s.dec)}</td>
      <td class="q">${esc(qualityText(s))}</td>
    </tr>`;
  }).join("");
  return `<table class="cascade-table">
    <thead><tr><th>${esc(tr("table.stage"))}</th><th>${esc(tr("table.sn"))}</th><th>${esc(tr("table.ra"))}</th><th>${esc(tr("table.dec"))}</th><th>${esc(tr("table.quality"))}</th></tr></thead>
    <tbody>${trs}</tbody>
  </table>${rows.length > 80 ? `<div class="cascade-empty">${esc(tr("cascade.layer.showing", { n: rows.length }))}</div>` : ""}`;
}

function renderEpoch(ep) {
  const layers = [
    [tr("cascade.layer.l1"), ep.counts.l1, sourceTable(ep.l1, "L1")],
    [tr("cascade.layer.residual"), ep.counts.residual, sourceTable(ep.residual, "residual")],
  ];
  const maskedTable = ep.counts.masked
    ? `<details class="cascade-layer"><summary>${esc(tr("cascade.layer.maskedCount", { n: ep.counts.masked }))}</summary>${sourceTable(ep.masked, "masked")}</details>`
    : `<div class="cascade-layer"><h4>${esc(tr("cascade.layer.masked"))}</h4><div class="cascade-empty">${esc(tr("cascade.layer.maskedNone"))}</div></div>`;
  return `<article class="cascade-epoch">
    <div class="cascade-epoch-head">
      <div><b>${esc(ep.epoch_id)}</b><br><span>MJD ${fmtNum(ep.mjd, 3)} · ${esc(ep.band || "?")} · ${esc(ep.provenance || "?")}</span></div>
      <div class="cascade-badges">
        <span class="cascade-badge">L1 ${ep.counts.l1}</span>
        <span class="cascade-badge masked">masked ${ep.counts.masked}</span>
        <span class="cascade-badge residual">residual ${ep.counts.residual}</span>
      </div>
    </div>
    ${layers.map(([title, n, table]) => `<div class="cascade-layer"><h4>${title}（${n}）</h4>${table}</div>`).join("")}
    ${maskedTable}
    ${ep.error ? `<div class="cascade-detail">${esc(tr("cascade.layer.failed", { err: window.I18n.serverText(ep.error) }))}</div>` : ""}
  </article>`;
}

function render(data) {
  state.data = data;
  state.byId = new Map();
  (data.epochs || []).forEach((ep) => {
    (ep.l1 || []).forEach((s) => state.byId.set(s.source_id, { ...s, mjd: ep.mjd, epoch_id: ep.epoch_id }));
    (ep.residual || []).forEach((s) => state.byId.set(s.source_id, { ...s, mjd: ep.mjd, epoch_id: ep.epoch_id }));
    (ep.masked || []).forEach((s) => state.byId.set(s.source_id, { ...s, mjd: ep.mjd, epoch_id: ep.epoch_id }));
  });
  const c = data.counts || {};
  const modeLabel = data.mode && window.I18n.hasKey(`mode.short.${data.mode}`) ? tr(`mode.short.${data.mode}`) : (data.mode || "?");
  $("#summary").classList.remove("hidden");
  $("#summary").innerHTML = `
    <div class="cascade-stat"><b>${c.n_epochs ?? 0}</b><span>${esc(tr("cascade.summary.epochs"))}</span></div>
    <div class="cascade-stat"><b>${c.l1 ?? 0}</b><span>${esc(tr("cascade.summary.l1"))}</span></div>
    <div class="cascade-stat"><b>${c.masked ?? 0}</b><span>${esc(tr("cascade.summary.masked"))}</span></div>
    <div class="cascade-stat"><b>${c.residual ?? 0}</b><span>${esc(tr("cascade.summary.residual"))}</span></div>
    <div class="cascade-stat"><b>${esc(modeLabel)}</b><span>${esc(tr("cascade.summary.mode"))}</span></div>`;
  note(`<b>${esc(modeLabel)}</b> · ${esc(timeFilterText(data.time_filter))} · ${esc(window.I18n.modeNote(data))}`);
  const oldDetail = $("#sourceDetail");
  if (oldDetail) oldDetail.remove();
  state.selected = null;
  const detail = document.createElement("section");
  detail.id = "sourceDetail";
  detail.className = "cascade-detail hidden";
  detail.setAttribute("aria-live", "polite");
  const before = $("#epochs");
  before.parentNode.insertBefore(detail, before);
  $("#epochs").innerHTML = (data.epochs || []).map(renderEpoch).join("") ||
    `<div class="cascade-empty">${esc(tr("cascade.empty"))}</div>`;
  $("#epochs").onclick = (e) => {
    const tr = e.target.closest("tr[data-source-id]");
    if (tr) selectSource(tr.dataset.sourceId);
  };
}

function selectSource(id) {
  const s = state.byId.get(id);
  if (!s) return;
  state.selected = s;
  document.querySelectorAll("#epochs tr.source-row").forEach((tr) => tr.classList.toggle("selected", tr.dataset.sourceId === id));
  const detail = $("#sourceDetail");
  if (!detail) return;
  detail.classList.remove("hidden");
  detail.innerHTML = `
    <b>${esc(tr("cascade.selected"))}</b> · ${esc(s.stage)} · <code>${esc(s.source_id)}</code><br>
    RA <b>${fmtNum(s.ra)}°</b> · Dec <b>${fmtNum(s.dec)}°</b> · S/N <b>${fmtSnr(s.snr)}</b> · MJD ${fmtNum(s.mjd, 3)}<br>
    ${s.parent ? `parent: <code>${esc(s.parent)}</code> · ` : ""}${s.masked_by ? `masked_by: <code>${esc(s.masked_by)}</code> · ` : ""}${s.reason ? `reason: ${esc(s.reason)} · ` : ""}quality: ${esc(qualityText(s))}
    <div class="cascade-ident" id="cascadeIdent">
      <button id="identifyCascade" class="ghost small" type="button">${esc(tr("cascade.identifyButton"))}</button>
      <span class="muted">${esc(tr("cascade.identifyHint"))}</span>
    </div>`;
  $("#identifyCascade").onclick = identifySelected;
}

async function identifySelected() {
  const s = state.selected;
  const box = $("#cascadeIdent");
  if (!s || !box) return;
  box.innerHTML = `<span class="muted">${esc(tr("cascade.querying"))}</span>`;
  try {
    const r = await fetch("/api/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ra: s.ra,
        dec: s.dec,
        mjd: s.mjd,
        sigma_arcsec: 0.5,
        detection_id: `cascade:${s.source_id}`,
        n_epochs: 1,
      }),
    });
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw apiError(e, tr("cascade.identifyRequestError"));
    }
    const status = d.status || "catalog_unavailable";
    const label = window.I18n.catalogLabel(status, d.catalog_release || d.release);
    const caveats = window.I18n.serverList(d.caveats).map((x) => `<div class="catalog-note">${esc(x)}</div>`).join("");
    box.innerHTML = `
      <span class="catalog-badge ${esc(status)}">${esc(label)}</span>
      <span class="muted">${esc(status)}${d.catalog_release ? ` · ${esc(d.catalog_release)}` : ""}${d.source_id ? ` · ${esc(d.source_id)}` : ""}</span>
      ${caveats}
      <div class="catalog-note">${esc(tr("catalog.disclaimer"))}</div>`;
  } catch (e) {
    box.innerHTML = `<span class="catalog-badge catalog_unavailable">${esc(window.I18n.catalogLabel("catalog_unavailable"))}</span>
      <span class="muted">${esc(window.I18n.errorText(e))}</span>
      <div class="catalog-note">${esc(tr("cascade.identifyUnavailable"))}</div>`;
  }
}

function selectedTarget() {
  const presetId = $("#targetSelect").value;
  const t = state.targets.find((x) => x.id === presetId);
  const ra = parseFloat($("#ra").value);
  const dec = parseFloat($("#dec").value);
  if (t && Number.isFinite(ra) && Number.isFinite(dec) && Math.abs(t.ra - ra) < 1e-6 && Math.abs(t.dec - dec) < 1e-6) {
    return { target_id: t.id };
  }
  return { ra, dec };
}

async function runCascade() {
  const fov = parseFloat($("#fov").value);
  if (!Number.isFinite(fov) || fov < 0.02 || fov > 3.0) {
    note(tr("note.fovInvalid", { value: esc($("#fov").value) }), "err");
    return;
  }
  const win = readTimeWindow();
  if (!win.ok) { note(win.error, "err"); return; }
  const sel = selectedTarget();
  const params = new URLSearchParams();
  if (sel.target_id) params.set("target_id", sel.target_id);
  else {
    if (!Number.isFinite(sel.ra) || !Number.isFinite(sel.dec)) {
      note(tr("note.raDecInvalid"), "err");
      return;
    }
    params.set("ra", String(sel.ra));
    params.set("dec", String(sel.dec));
  }
  params.set("fov", String(fov));
  if (win.t0 != null) params.set("t0", String(win.t0));
  if (win.t1 != null) params.set("t1", String(win.t1));
  updateViewLink(sel, fov, win);
  const btn = $("#runCascade");
  btn.disabled = true;
  note(tr("cascade.noteRunning"));
  try {
    const r = await fetch("/api/cascade?" + params.toString());
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw apiError(e, tr("cascade.runError", { msg: "" }));
    }
    render(d);
    const u = new URL(location.href);
    u.searchParams.set("lang", window.I18n.lang);
    if (sel.target_id) {
      u.searchParams.set("target_id", sel.target_id);
      u.searchParams.delete("ra");
      u.searchParams.delete("dec");
    } else {
      u.searchParams.set("ra", String(sel.ra));
      u.searchParams.set("dec", String(sel.dec));
      u.searchParams.delete("target_id");
    }
    u.searchParams.set("fov", String(fov));
    if (win.t0 != null) u.searchParams.set("t0", String(win.t0)); else u.searchParams.delete("t0");
    if (win.t1 != null) u.searchParams.set("t1", String(win.t1)); else u.searchParams.delete("t1");
    history.replaceState(null, "", u.toString());
  } catch (e) {
    $("#summary").classList.add("hidden");
    $("#epochs").innerHTML = "";
    note(tr("cascade.runError", { msg: esc(window.I18n.errorText(e)) }), "err");
  } finally {
    btn.disabled = false;
  }
}

function fillTargets(targets) {
  state.targets = targets || [];
  const sel = $("#targetSelect");
  const current = sel.value;
  sel.innerHTML = `<option value="">${esc(tr("cascade.coordPlaceholder"))}</option>` + state.targets.map((t) =>
    `<option value="${esc(t.id)}">${esc(targetDisplayName(t))}</option>`
  ).join("");
  if (current != null) sel.value = current;
  sel.onchange = () => {
    const t = state.targets.find((x) => x.id === sel.value);
    if (t) {
      $("#ra").value = t.ra;
      $("#dec").value = t.dec;
    }
    if (sel.value) runCascade();
  };
}

async function init() {
  try {
    const r = await fetch("/api/targets");
    const d = await r.json();
    fillTargets(d.targets || []);
  } catch (e) {
    note(tr("cascade.targetListFail", { msg: window.I18n.errorText(e) }), "err");
  }
  const u = new URL(location.href);
  const tid = u.searchParams.get("target_id");
  const ra = u.searchParams.get("ra");
  const dec = u.searchParams.get("dec");
  const fov = u.searchParams.get("fov");
  if (fov) $("#fov").value = fov;
  u.searchParams.set("lang", window.I18n.lang);
  const t0 = u.searchParams.get("t0");
  const t1 = u.searchParams.get("t1");
  if (t0) $("#t0").value = t0;
  if (t1) $("#t1").value = t1;
  if (t0 || t1 || u.searchParams.get("from") === "view") $("#viewLink").classList.remove("hidden");
  if (ra && dec) {
    $("#targetSelect").value = "";
    $("#ra").value = ra;
    $("#dec").value = dec;
  } else {
    const id = tid || "barnard";
    const t = state.targets.find((x) => x.id === id) || state.targets[0];
    if (t) {
      $("#targetSelect").value = t.id;
      $("#ra").value = t.ra;
      $("#dec").value = t.dec;
    }
  }
  $("#runCascade").onclick = runCascade;
  updateViewLink(selectedTarget(), parseFloat($("#fov").value), readTimeWindow());
  runCascade();
}

window.I18n.onChange(() => {
  fillTargets(state.targets);
  if (state.data) {
    render(state.data);
    if (state.selected) selectSource(state.selected.source_id);
  }
});

init();
