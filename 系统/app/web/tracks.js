/* MoveScope · 跨历元轨迹候选页
 * D0–D4 是候选分级，不是发现确认；节点识别走 /api/identify。
 */
const $ = (sel) => document.querySelector(sel);
const state = { targets: [], tracks: [], selected: null };
const LEVEL_TEXT = {
  D0: "D0 artifact",
  D1: "D1 known catalog",
  D2: "D2 not implemented",
  D3: "D3 unknown moving",
  D4: "D4 unknown stationary",
};

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(v, digits = 5) { return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits); }
function note(html, cls = "") { const el = $("#tracksNote"); el.innerHTML = html; el.className = "note " + cls; }
function qualityText(n) { return Array.isArray(n.quality) && n.quality.length ? n.quality.join(" · ") : "—"; }

function readTimeWindow() {
  const t0 = $("#t0").value.trim();
  const t1 = $("#t1").value.trim();
  if (!t0 && !t1) return { ok: true, t0: null, t1: null };
  if (!t0 || !t1) return { ok: false, error: "t0 与 t1 必须同时填写（MJD）。" };
  const a = Number(t0), b = Number(t1);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return { ok: false, error: "t0/t1 必须是 MJD 数字。" };
  if (a > b) return { ok: false, error: "t0 必须 ≤ t1。" };
  return { ok: true, t0: a, t1: b };
}

function timeFilterText(tf) {
  if (!tf || !tf.filtered) return tf && tf.requested != null ? `全时段 ${tf.requested} 期` : "全时段";
  return `来自视图 / 时间窗口 MJD ${Number(tf.t0).toFixed(3)}–${Number(tf.t1).toFixed(3)} / 保留 ${tf.kept}/${tf.requested} 期`;
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
    <td><button type="button" class="ghost small" data-ident-track="${esc(track.track_id)}" data-ident-node="${i}">识别</button></td>
  </tr>`).join("");
}

function trackCard(t) {
  const caveats = (t.caveats || []).length ? `<div class="track-caveats">${t.caveats.map((c) => esc(c)).join(" · ")}</div>` : "";
  return `<article class="track-card ${esc(t.level)}">
    <div class="track-head">
      <div><b>${esc(t.track_id)}</b><br><span>${esc(t.model)} · ${t.n_epochs} 期 · confidence ${fmt(t.confidence, 2)}</span></div>
      <div class="track-badges">
        <span class="track-level ${esc(t.level)}">${esc(t.level)} · ${esc(LEVEL_TEXT[t.level] || t.level)}</span>
        <span class="cascade-badge">catalog: ${esc(t.catalog || "none")}</span>
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
      <summary>节点 ${t.n_epochs} 个 · 展开</summary>
      <table class="track-node-table">
        <thead><tr><th>epoch</th><th>stage</th><th>RA</th><th>Dec</th><th>S/N</th><th>quality</th><th></th></tr></thead>
        <tbody>${nodeRows(t)}</tbody>
      </table>
      <div class="track-ident" id="trackIdent-${esc(t.track_id)}"></div>
    </details>
  </article>`;
}

function render(data) {
  state.tracks = data.tracks || [];
  const c = data.counts || {};
  $("#trackSummary").classList.remove("hidden");
  $("#trackSummary").innerHTML = ["D0", "D1", "D2", "D3", "D4"].map((lv) =>
    `<div class="cascade-stat"><b>${c[lv] ?? 0}</b><span>${lv} ${esc((LEVEL_TEXT[lv] || "").split(" ").slice(1).join(" "))}</span></div>`
  ).join("");
  note(`<b>${esc(data.mode || "?")}</b> · ${timeFilterText(data.time_filter)} · ${data.insufficient ? "<b>insufficient</b> · " : ""}${cascadeText(data.cascade_counts)} · ${esc(data.note || "")}`);
  const shown = state.tracks.slice(0, 120);
  $("#trackList").innerHTML = shown.length ? shown.map(trackCard).join("") :
    '<div class="track-empty">没有 tracklet。数据不足/没有节点时不伪造轨迹。</div>';
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
  box.innerHTML = '<span class="muted">正在查询星表…</span>';
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
      throw new Error((e && (e.message || e.code)) || "identify 请求失败");
    }
    const status = d.status || "catalog_unavailable";
    const label = d.label_zh || (status === "catalog_unavailable" ? "星表不可用" : status);
    box.innerHTML = `<span class="catalog-badge ${esc(status)}">${esc(label)}</span>
      <span class="muted">${esc(status)}${d.catalog_release ? " · " + esc(d.catalog_release) : ""}${d.source_id ? " · " + esc(d.source_id) : ""}</span>
      ${(d.caveats || []).map((x) => `<div class="catalog-note">${esc(x)}</div>`).join("")}`;
  } catch (e) {
    box.innerHTML = `<span class="catalog-badge catalog_unavailable">星表不可用</span>
      <span class="muted">${esc(e.message)}</span>
      <div class="catalog-note">星表不可用，不能判断节点是否已知；这不是“未匹配”。</div>`;
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
    note("FOV 需在 0.02–3.0 度之间。", "err");
    return;
  }
  const win = readTimeWindow();
  if (!win.ok) { note(win.error, "err"); return; }
  const sel = selectedTarget();
  const params = new URLSearchParams();
  if (sel.target_id) params.set("target_id", sel.target_id);
  else {
    if (!Number.isFinite(sel.ra) || !Number.isFinite(sel.dec)) {
      note("请输入有效的 RA / Dec，或选择一个预设目标。", "err");
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
  note("正在取历元、跑 cascade 并关联 tracklet…");
  try {
    const r = await fetch("/api/tracks?" + params.toString());
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw new Error((e && (e.message || e.code)) || "轨迹接口返回错误");
    }
    render(d);
    const u = new URL(location.href);
    if (sel.target_id) { u.searchParams.set("target_id", sel.target_id); u.searchParams.delete("ra"); u.searchParams.delete("dec"); }
    else { u.searchParams.set("ra", String(sel.ra)); u.searchParams.set("dec", String(sel.dec)); u.searchParams.delete("target_id"); }
    u.searchParams.set("fov", String(fov));
    if (win.t0 != null) u.searchParams.set("t0", String(win.t0)); else u.searchParams.delete("t0");
    if (win.t1 != null) u.searchParams.set("t1", String(win.t1)); else u.searchParams.delete("t1");
    history.replaceState(null, "", u.toString());
  } catch (e) {
    $("#trackSummary").classList.add("hidden");
    $("#trackList").innerHTML = "";
    note(`轨迹不可用：${esc(e.message)}。没有展示 SIM/伪造轨迹。`, "err");
  } finally {
    btn.disabled = false;
  }
}

function fillTargets(targets) {
  state.targets = targets || [];
  const sel = $("#targetSelect");
  sel.innerHTML = '<option value="">自定义坐标</option>' + state.targets.map((t) =>
    `<option value="${esc(t.id)}">${esc(t.name_zh || t.name)}</option>`
  ).join("");
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
    note("目标列表不可用：" + e.message, "err");
  }
  const u = new URL(location.href);
  const tid = u.searchParams.get("target_id");
  const ra = u.searchParams.get("ra");
  const dec = u.searchParams.get("dec");
  const fov = u.searchParams.get("fov");
  if (fov) $("#fov").value = fov;
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

init();
