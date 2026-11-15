/* MoveScope · 级联/残差页（无构建，原生 JS）
 *
 * 只做展示与 /api/cascade、/api/identify 调用：
 * - 不把 residual 说成新天体；
 * - 不在前端做星表匹配/运动判定；
 * - 星表不可用时明确显示，不写成“未匹配”。
 */
const $ = (sel) => document.querySelector(sel);
const state = {
  targets: [],
  data: null,
  selected: null,
  byId: new Map(),
};

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtNum(v, digits = 5) {
  return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits);
}

function fmtSnr(v) {
  return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(1);
}

function note(html, cls = "") {
  const el = $("#cascadeNote");
  el.innerHTML = html;
  el.className = "note " + cls;
}

function qualityText(src) {
  const q = Array.isArray(src.quality) ? src.quality : [];
  return q.length ? q.join(" · ") : "—";
}

function sourceTable(rows, stageLabel) {
  if (!rows || !rows.length) return '<div class="cascade-empty">本层没有源</div>';
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
    <thead><tr><th>stage</th><th>S/N</th><th>RA</th><th>Dec</th><th>quality</th></tr></thead>
    <tbody>${trs}</tbody>
  </table>${rows.length > 80 ? `<div class="cascade-empty">仅显示前 80 / 共 ${rows.length} 个；计数为全量。</div>` : ""}`;
}

function renderEpoch(ep) {
  const layers = [
    ["L1 检测源", ep.counts.l1, sourceTable(ep.l1, "L1")],
    ["残差候选（residual）", ep.counts.residual, sourceTable(ep.residual, "residual")],
  ];
  const maskedTable = ep.counts.masked
    ? `<details class="cascade-layer"><summary>被掩膜 / 已检测（${ep.counts.masked}）— reason=already_detected</summary>${sourceTable(ep.masked, "masked")}</details>`
    : '<div class="cascade-layer"><h4>被掩膜 / 已检测</h4><div class="cascade-empty">本历元没有 L1 源需要掩膜</div></div>';
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
    ${ep.error ? `<div class="cascade-detail">本历元级联失败：${esc(ep.error)}</div>` : ""}
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
  $("#summary").classList.remove("hidden");
  $("#summary").innerHTML = `
    <div class="cascade-stat"><b>${c.n_epochs ?? 0}</b><span>历元</span></div>
    <div class="cascade-stat"><b>${c.l1 ?? 0}</b><span>L1 源</span></div>
    <div class="cascade-stat"><b>${c.masked ?? 0}</b><span>Masked</span></div>
    <div class="cascade-stat"><b>${c.residual ?? 0}</b><span>Residual</span></div>
    <div class="cascade-stat"><b>${esc(data.mode || "?")}</b><span>数据模式</span></div>`;
  note(`<b>${esc(data.mode || "?")}</b> · ${c.n_epochs ?? 0} 历元 · ${esc(data.note || "")}`);
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
    '<div class="cascade-empty">没有历元。</div>';
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
    <b>已选源</b> · ${esc(s.stage)} · <code>${esc(s.source_id)}</code><br>
    RA <b>${fmtNum(s.ra)}°</b> · Dec <b>${fmtNum(s.dec)}°</b> · S/N <b>${fmtSnr(s.snr)}</b> · MJD ${fmtNum(s.mjd, 3)}<br>
    ${s.parent ? `parent: <code>${esc(s.parent)}</code> · ` : ""}${s.masked_by ? `masked_by: <code>${esc(s.masked_by)}</code> · ` : ""}${s.reason ? `reason: ${esc(s.reason)} · ` : ""}quality: ${esc(qualityText(s))}
    <div class="cascade-ident" id="cascadeIdent">
      <button id="identifyCascade" class="ghost small" type="button">用 /api/identify 识别</button>
      <span class="muted">只查星表身份；残差候选不是新天体。</span>
    </div>`;
  $("#identifyCascade").onclick = identifySelected;
}

async function identifySelected() {
  const s = state.selected;
  const box = $("#cascadeIdent");
  if (!s || !box) return;
  box.innerHTML = '<span class="muted">正在查询星表…</span>';
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
      throw new Error((e && (e.message || e.code)) || "identify 请求失败");
    }
    const status = d.status || "catalog_unavailable";
    const label = d.label_zh || (status === "catalog_unavailable" ? "星表不可用" : status);
    const caveats = (d.caveats || []).map((x) => `<div class="catalog-note">${esc(x)}</div>`).join("");
    box.innerHTML = `
      <span class="catalog-badge ${esc(status)}">${esc(label)}</span>
      <span class="muted">${esc(status)}${d.catalog_release ? ` · ${esc(d.catalog_release)}` : ""}${d.source_id ? ` · ${esc(d.source_id)}` : ""}</span>
      ${caveats}
      <div class="catalog-note">${esc(d.disclaimer || "残差候选不是新天体；未匹配不等于新星。")}</div>`;
  } catch (e) {
    box.innerHTML = `<span class="catalog-badge catalog_unavailable">星表不可用</span>
      <span class="muted">${esc(e.message)}</span>
      <div class="catalog-note">星表不可用，不能判断该源是否已知；这不是“未匹配”。</div>`;
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
    note("FOV 需在 0.02–3.0 度之间。", "err");
    return;
  }
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
  const btn = $("#runCascade");
  btn.disabled = true;
  note("正在取历元并跑级联层…");
  try {
    const r = await fetch("/api/cascade?" + params.toString());
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw new Error((e && (e.message || e.code)) || "级联接口返回错误");
    }
    render(d);
    const u = new URL(location.href);
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
    history.replaceState(null, "", u.toString());
  } catch (e) {
    $("#summary").classList.add("hidden");
    $("#epochs").innerHTML = "";
    note(`级联不可用：${esc(e.message)}。没有展示 SIM/伪造源。`, "err");
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
    note("目标列表不可用：" + e.message, "err");
  }
  const u = new URL(location.href);
  const tid = u.searchParams.get("target_id");
  const ra = u.searchParams.get("ra");
  const dec = u.searchParams.get("dec");
  const fov = u.searchParams.get("fov");
  if (fov) $("#fov").value = fov;
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
  runCascade();
}

init();
