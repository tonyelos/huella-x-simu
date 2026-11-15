/* MoveScope · 跨历元轨迹候选页
 * D0–D4 是候选分级，不是发现确认；节点识别走 /api/identify。
 */
window.I18n.init();
const tr = (key, params) => window.I18n.t(key, params);
const $ = (sel) => document.querySelector(sel);
const state = { targets: [], tracks: [], selected: null, data: null };

function levelText(level) {
  const key = `tracks.level.${level}`;
  return window.I18n.hasKey(key) ? tr(key) : level;
}

function targetDisplayName(t) {
  if (!t) return "";
  const fallback = { m101: "target.m101", campo_a: "target.campo_a" }[t.id];
  if (fallback && window.I18n.hasKey(fallback)) return tr(fallback);
  return /[\u4e00-\u9fff]/.test(String(t.name || "")) ? (fallback ? tr(fallback) : String(t.name || t.id || "")) : (t.name || t.id || "");
}

function apiError(e, fallback) {
  const err = new Error((e && (e.message || e.code)) || fallback || tr("apiError.generic"));
  if (e && e.code) err.code = e.code;
  return err;
}

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(v, digits = 5) { return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits); }
function note(html, cls = "") { const el = $("#tracksNote"); el.innerHTML = html; el.className = "note " + cls; }
function qualityText(n) { return window.I18n.qualityText(n); }

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

function nodeRows(track) {
  return (track.nodes || []).map((n, i) => `<tr>
    <td>${esc(n.epoch_id || "—")}</td>
    <td>${esc(n.stage || "—")}</td>
    <td>${fmt(n.ra)}</td>
    <td>${fmt(n.dec)}</td>
    <td>${fmt(n.snr, 1)}</td>
    <td>${esc(qualityText(n))}</td>
    <td><button type="button" class="ghost small" data-ident-track="${esc(track.track_id)}" data-ident-node="${i}">${esc(tr("tracks.identifyButton"))}</button></td>
  </tr>`).join("");
}

function trackCard(t) {
  const caveats = (t.caveats || []).length ? `<div class="track-caveats">${t.caveats.map((c) => esc(window.I18n.serverText(c))).join(" · ")}</div>` : "";
  return `<article class="track-card ${esc(t.level)}">
    <div class="track-head">
      <div><b>${esc(t.track_id)}</b><br><span>${esc(t.model)} · ${esc(tr("layer.stats.epochs", { n: t.n_epochs }))} · ${esc(tr("tracks.confidence"))} ${fmt(t.confidence, 2)}</span></div>
      <div class="track-badges">
        <span class="track-level ${esc(t.level)}">${esc(levelText(t.level) || t.level)}</span>
        <span class="cascade-badge">${esc(tr("tracks.catalogLabel"))}: ${esc(t.catalog || "none")}</span>
      </div>
    </div>
    <div class="track-body">
      <div><span>RA</span>${fmt(t.ra)}°</div>
      <div><span>Dec</span>${fmt(t.dec)}°</div>
      <div><span>μ</span>${fmt(t.mu_arcsec_yr, 2)}″/yr</div>
      <div><span>PA</span>${t.pa_deg == null ? "—" : fmt(t.pa_deg, 1) + "°"}</div>
      ${caveats}
    </div>
    <details class="track-nodes">
      <summary>${esc(tr("tracks.expand", { n: t.n_epochs }))}</summary>
      <table class="track-node-table">
        <thead><tr><th>${esc(tr("table.epoch"))}</th><th>${esc(tr("table.stage"))}</th><th>${esc(tr("table.ra"))}</th><th>${esc(tr("table.dec"))}</th><th>${esc(tr("table.sn"))}</th><th>${esc(tr("table.quality"))}</th><th></th></tr></thead>
        <tbody>${nodeRows(t)}</tbody>
      </table>
      <div class="track-ident" id="trackIdent-${esc(t.track_id)}"></div>
    </details>
  </article>`;
}

function render(data) {
  state.data = data;
  state.tracks = data.tracks || [];
  const c = data.counts || {};
  $("#trackSummary").classList.remove("hidden");
  $("#trackSummary").innerHTML = ["D0", "D1", "D2", "D3", "D4"].map((lv) =>
    `<div class="cascade-stat"><b>${c[lv] ?? 0}</b><span>${esc(levelText(lv))}</span></div>`
  ).join("");
  const insufficientNote = data.insufficient && data.time_filter
    ? " " + tr("tracks.insufficientNote", { kept: data.time_filter.kept })
    : "";
  const modeLabel = data.mode && window.I18n.hasKey(`mode.short.${data.mode}`) ? tr(`mode.short.${data.mode}`) : (data.mode || "?");
  note(`<b>${esc(modeLabel)}</b> · ${esc(timeFilterText(data.time_filter))} · ${data.insufficient ? "<b>insufficient</b> · " : ""}${esc(cascadeText(data.cascade_counts))} · ${esc(window.I18n.modeNote(data) + insufficientNote)}`);
  const shown = state.tracks.slice(0, 120);
  $("#trackList").innerHTML = shown.length ? shown.map(trackCard).join("") :
    `<div class="track-empty">${esc(tr("tracks.empty"))}</div>`;
  $("#trackList").onclick = (e) => {
    const btn = e.target.closest("button[data-ident-track]");
    if (!btn) return;
    const track = state.tracks.find((x) => x.track_id === btn.dataset.identTrack);
    const node = track && track.nodes[Number(btn.dataset.identNode)];
    if (track && node) identifyNode(track, node);
  };
}

function cascadeText(c) {
  if (!c) return "cascade —";
  return `cascade L1 ${c.l1 ?? 0} / residual ${c.residual ?? 0}`;
}

async function identifyNode(track, node) {
  const box = document.getElementById(`trackIdent-${track.track_id}`);
  if (!box) return;
  box.innerHTML = `<span class="muted">${esc(tr("tracks.querying"))}</span>`;
  try {
    const r = await fetch("/api/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ra: node.ra, dec: node.dec, mjd: node.mjd, sigma_arcsec: 0.5,
        detection_id: `track:${track.track_id}:${node.source_id}`,
        n_epochs: 1,
      }),
    });
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw apiError(e, tr("tracks.identifyRequestError"));
    }
    const status = d.status || "catalog_unavailable";
    const label = window.I18n.catalogLabel(status, d.catalog_release || d.release);
    box.innerHTML = `<span class="catalog-badge ${esc(status)}">${esc(label)}</span>
      <span class="muted">${esc(status)}${d.catalog_release ? " · " + esc(d.catalog_release) : ""}${d.source_id ? " · " + esc(d.source_id) : ""}</span>
      ${window.I18n.serverList(d.caveats).map((x) => `<div class="catalog-note">${esc(x)}</div>`).join("")}`;
  } catch (e) {
    box.innerHTML = `<span class="catalog-badge catalog_unavailable">${esc(window.I18n.catalogLabel("catalog_unavailable"))}</span>
      <span class="muted">${esc(window.I18n.errorText(e))}</span>
      <div class="catalog-note">${esc(tr("tracks.identifyUnavailable"))}</div>`;
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

async function runTracks() {
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
  const btn = $("#runTracks");
  btn.disabled = true;
  note(tr("tracks.noteRunning"));
  try {
    const r = await fetch("/api/tracks?" + params.toString());
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw apiError(e, tr("tracks.runError", { msg: "" }));
    }
    render(d);
    const u = new URL(location.href);
    u.searchParams.set("lang", window.I18n.lang);
    if (sel.target_id) { u.searchParams.set("target_id", sel.target_id); u.searchParams.delete("ra"); u.searchParams.delete("dec"); }
    else { u.searchParams.set("ra", String(sel.ra)); u.searchParams.set("dec", String(sel.dec)); u.searchParams.delete("target_id"); }
    u.searchParams.set("fov", String(fov));
    if (win.t0 != null) u.searchParams.set("t0", String(win.t0)); else u.searchParams.delete("t0");
    if (win.t1 != null) u.searchParams.set("t1", String(win.t1)); else u.searchParams.delete("t1");
    history.replaceState(null, "", u.toString());
  } catch (e) {
    $("#trackSummary").classList.add("hidden");
    $("#trackList").innerHTML = "";
    note(tr("tracks.runError", { msg: esc(window.I18n.errorText(e)) }), "err");
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
    if (t) { $("#ra").value = t.ra; $("#dec").value = t.dec; }
    if (sel.value) runTracks();
  };
}

async function init() {
  try {
    const r = await fetch("/api/targets");
    const d = await r.json();
    fillTargets(d.targets || []);
  } catch (e) {
    note(tr("tracks.targetListFail", { msg: window.I18n.errorText(e) }), "err");
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
    const t = state.targets.find((x) => x.id === (tid || "barnard")) || state.targets[0];
    if (t) { $("#targetSelect").value = t.id; $("#ra").value = t.ra; $("#dec").value = t.dec; }
  }
  $("#runTracks").onclick = runTracks;
  updateViewLink(selectedTarget(), parseFloat($("#fov").value), readTimeWindow());
  runTracks();
}

window.I18n.onChange(() => {
  fillTargets(state.targets);
  if (state.data) render(state.data);
});

init();
