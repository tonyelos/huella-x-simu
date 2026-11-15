/* MoveScope 前端：三栏、Canvas 天区视图、闪烁/叠加/相减/轨迹、鉴定卡 */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  targets: [],
  analysis: null,
  frame: 0,
  mode: "blink",
  playing: false,
  timer: null,
  main: [],
  diff: [],
  stack: null,
  selected: null,
  loading: false,
  sources: [],
  locked: null, // 已锁定的检测源（含 ra/dec/snr/quality）
  lockedIdentification: null, // 锁定源单独的 /api/identify 结果
  lockedIdentificationKey: null,
  lockedIdentifying: false,
  hover: null, // { source, frame } 当前 hover 的源
  hoverKey: null,
  lockRequest: null, // URL 带 lock=1 时，分析完成后按坐标重新锁定
  anyfieldSeq: 0, // C 覆盖握手请求序号：旧响应不得覆盖新坐标
  anyfieldContext: null, // {ra, dec, fov} 当前 C 覆盖卡对应的查询
  anyfieldSources: [],
  anyfieldBatch: { byId: {}, counts: null, catalogStatus: null, error: null },
  aSourceBatch: { byId: {}, counts: null, error: null },
  candidateDirectory: null,
  layerMode: "browse", // browse | cascade | tracks
  cascadeData: null,
  tracksData: null,
  layerKeys: { cascade: null, tracks: null },
  layerLoading: false,
  layerHits: [],
  trackHits: [],
  trackSegments: [],
  selectedTrack: null,
  layerVis: { l1: true, masked: true, residual: true },
  fromView: false,
  analysisFov: null,
};

const CLASS_META = {
  L: { letter: "L", label: "匀速直线运动", sub: "高自行恒星 / 褐矮星候选" },
  V: { letter: "V", label: "变光", sub: "位置不动，亮度在变" },
  S: { letter: "S", label: "静止", sub: "无明显位移或变光" },
  A: { letter: "A", label: "加速 / 弯曲", sub: "太阳系天体或轨道运动候选" },
  P: { letter: "P", label: "视差", sub: "周年视运动" },
  O: { letter: "O", label: "轨道运动", sub: "束缚双星/伴星" },
  U: { letter: "U", label: "不可判定", sub: "数据不足以判断" },
};

/* 星表识别（阶段 D MVP）：只陈述“能否被星表解释”，不做发现声明。 */
const CATALOG_TEXT = {
  known_star: "Gaia DR3 已知",
  ambiguous: "多候选 ambiguous",
  uncatalogued_candidate: "未收录候选（未经确认）",
  catalog_unavailable: "星表不可用",
  insufficient_data: "数据不足",
};

const CATALOG_REASON_TEXT = {
  CATALOG_UNAVAILABLE: "星表服务不可达或返回 503",
  TARGET_NOT_DETECTED: "目标没有被探测到",
  FETCH_FAILED: "真实切片取数失败",
  CATALOG_PARTIAL: "星表覆盖不完整，0 候选不能当“未收录”",
  NO_CACHED_FIELD: "查询点不在已缓存的星表场覆盖内",
  CATALOG_UNREACHABLE: "星表服务不可达",
  MALFORMED_RESPONSE: "星表响应格式异常",
  SINGLE_EPOCH: "仅单期，无法核对自行",
  NO_CATALOG_MATCH: "星表查询成功，但没有候选",
  MULTIPLE_CANDIDATES: "匹配半径内有多颗星表源",
  PM_INCONSISTENT: "位置匹配但自行不一致",
  CATALOG_ID_MISSING: "候选缺少 catalog_id",
  IDENTIFICATION_ERROR: "识别过程出现内部错误，已降级",
  NO_TARGET_MEASUREMENT: "没有目标实测位置",
  SINGLE_CATALOG_MATCH: "位置匹配，星表 ID 已确认",
  NO_PM_CHECK: "未核对自行（无可用 PM）",
  HIGH_PM: "高自行目标",
};

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function catalogText(ident) {
  if (!ident) return "未识别";
  return ident.label_zh || CATALOG_TEXT[ident.status] || ident.status || "未识别";
}

function catalogReasonText(ident) {
  if (!ident) return "";
  const key = ident.reason === "CATALOG_UNAVAILABLE" ? (ident.catalog_reason || ident.reason) : ident.reason;
  return CATALOG_REASON_TEXT[key] || CATALOG_REASON_TEXT[ident.reason] || key || "";
}

/* C 任意天区 P0：只做覆盖握手，不生成源列表/模拟帧，不阻塞原分析。 */
const ANYFIELD_TEXT = {
  ok: "C 覆盖可用",
  no_coverage: "C 确认无覆盖",
  index_incomplete: "C 索引不完整",
  unavailable: "C 服务不可用",
};

function hideAnyfield() {
  state.anyfieldSeq += 1;
  state.anyfieldContext = null;
  state.anyfieldSources = [];
  state.anyfieldBatch = { byId: {}, counts: null, catalogStatus: null, error: null };
  const card = $("#anyfieldCard");
  if (card) card.classList.add("hidden");
  const btn = $("#loadCSourcesBtn");
  if (btn) btn.classList.add("hidden");
  const batchBtn = $("#batchCSourcesBtn");
  if (batchBtn) batchBtn.classList.add("hidden");
  const batchGroup = $("#anyfieldBatchActions");
  if (batchGroup) batchGroup.classList.add("hidden");
  const batchNote = $("#anyfieldBatchNote");
  if (batchNote) { batchNote.classList.add("hidden"); batchNote.innerHTML = ""; }
  const list = $("#anyfieldSources");
  if (list) {
    list.classList.add("hidden");
    list.innerHTML = "";
  }
}

function renderAnyfield(data, httpStatus) {
  const card = $("#anyfieldCard");
  const badge = $("#anyfieldBadge");
  const body = $("#anyfieldBody");
  if (!card || !badge || !body) return;
  const status = data && data.status;
  card.classList.remove("hidden");
  card.dataset.status = status || "unavailable";
  const label = ANYFIELD_TEXT[status] || "C 状态未知";
  badge.className = "anyfield-badge " + (status || "unavailable");
  badge.textContent = label;
  const loadBtn = $("#loadCSourcesBtn");
  const list = $("#anyfieldSources");
  if (status === "ok" && httpStatus === 200) {
    if (loadBtn) loadBtn.classList.remove("hidden");
  } else {
    if (loadBtn) loadBtn.classList.add("hidden");
    const batchBtn = $("#batchCSourcesBtn");
    if (batchBtn) batchBtn.classList.add("hidden");
    const batchGroup = $("#anyfieldBatchActions");
    if (batchGroup) batchGroup.classList.add("hidden");
    const batchNote = $("#anyfieldBatchNote");
    if (batchNote) { batchNote.classList.add("hidden"); batchNote.innerHTML = ""; }
    if (list) {
      list.classList.add("hidden");
      list.innerHTML = "";
    }
    state.anyfieldSources = [];
    state.anyfieldBatch = { byId: {}, counts: null, catalogStatus: null, error: null };
  }
  if (status === "ok" && httpStatus === 200) {
    const n = Number(data.n_epochs || 0);
    const method = data.footprint_method ? `<code>${esc(data.footprint_method)}</code>` : "—";
    const complete = data.index_completeness ? ` · 索引 ${esc(data.index_completeness)}` : "";
    body.innerHTML = `C 本地覆盖：<b>${n}</b> 期 · footprint ${method}${complete}`;
  } else if (status === "no_coverage" || (httpStatus === 422 && !status)) {
    body.innerHTML = "C 在 complete region 内确认无覆盖；不改变现有本地数据/分析结论。";
  } else if (status === "index_incomplete") {
    body.innerHTML = "C 索引不完整，使用现有本地数据。C 的缺口不等于“确认无覆盖”。";
  } else {
    body.innerHTML = "C 服务不可用，使用现有本地数据。";
  }
}

async function refreshAnyfield(ra, dec, fov) {
  const seq = ++state.anyfieldSeq;
  state.anyfieldContext = { ra: Number(ra), dec: Number(dec), fov: Number(fov) };
  state.anyfieldSources = [];
  state.anyfieldBatch = { byId: {}, counts: null, catalogStatus: null, error: null };
  const batchBtn = $("#batchCSourcesBtn");
  if (batchBtn) batchBtn.classList.add("hidden");
  const batchGroup = $("#anyfieldBatchActions");
  if (batchGroup) batchGroup.classList.add("hidden");
  const batchNote = $("#anyfieldBatchNote");
  if (batchNote) { batchNote.classList.add("hidden"); batchNote.innerHTML = ""; }
  const list = $("#anyfieldSources");
  if (list) {
    list.classList.add("hidden");
    list.innerHTML = "";
  }
  const card = $("#anyfieldCard");
  const badge = $("#anyfieldBadge");
  const body = $("#anyfieldBody");
  if (!card || !badge || !body) return;
  card.classList.remove("hidden");
  card.dataset.status = "loading";
  badge.className = "anyfield-badge";
  badge.textContent = "查询 C 覆盖…";
  body.textContent = "可选增强：C 不可用时会继续使用现有本地数据。";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const url = `/api/field?ra=${encodeURIComponent(ra)}&dec=${encodeURIComponent(dec)}&fov=${encodeURIComponent(fov)}`;
    const r = await fetch(url, { signal: ctrl.signal });
    let data = {};
    try {
      data = await r.json();
    } catch (e) {
      data = {};
    }
    if (seq !== state.anyfieldSeq) return;
    renderAnyfield(data, r.status);
  } catch (e) {
    if (seq !== state.anyfieldSeq) return;
    renderAnyfield({ status: "unavailable" }, 0);
  } finally {
    clearTimeout(timer);
  }
}

async function loadCSources() {
  const ctx = state.anyfieldContext;
  if (!ctx) return;
  const btn = $("#loadCSourcesBtn");
  const box = $("#anyfieldSources");
  if (!btn || !box) return;
  btn.disabled = true;
  btn.textContent = "加载 C 源列表…";
  box.classList.remove("hidden");
  box.innerHTML = '<div class="anyfield-source-note">正在读取 C 已 ingest 的源…</div>';
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const url = `/api/sources?ra=${encodeURIComponent(ctx.ra)}&dec=${encodeURIComponent(ctx.dec)}&fov=${encodeURIComponent(ctx.fov)}&limit=200`;
    const r = await fetch(url, { signal: ctrl.signal });
    let data = {};
    try {
      data = await r.json();
    } catch (e) {
      data = {};
    }
    renderCSources(data, r.status);
  } catch (e) {
    renderCSources({ status: "unavailable" }, 0);
  } finally {
    clearTimeout(timer);
    btn.disabled = false;
    btn.textContent = "加载 C 源列表";
  }
}

function batchBadgeFor(result) {
  if (!result) return "";
  if (result.match_status === "no_match" || result.status === "uncatalogued_candidate") {
    return `<span class="batch-badge no_match" title="未收录候选，不是未知星体">no_match</span>`;
  }
  if (result.status === "known_star") return '<span class="batch-badge known_star">known</span>';
  if (result.status === "ambiguous") return '<span class="batch-badge ambiguous">ambiguous</span>';
  if (result.status === "catalog_unavailable") return '<span class="batch-badge catalog_unavailable" title="星表不可用">unavailable</span>';
  if (result.status === "insufficient_data") return '<span class="batch-badge insufficient_data">insufficient</span>';
  return "";
}

function batchSummary(counts) {
  if (!counts) return "";
  return `known ${counts.known_star || 0} / ambiguous ${counts.ambiguous || 0} / no_match ${counts.no_match || 0}`;
}

async function batchIdentifyCSources() {
  const sources = state.anyfieldSources || [];
  const btn = $("#batchCSourcesBtn");
  const note = $("#anyfieldBatchNote");
  if (!sources.length) return;
  if (btn) { btn.disabled = true; btn.textContent = "批量识别中…"; }
  ["#exportCSourcesBtn", "#exportCJsonBtn"].forEach((sel) => { const b = $(sel); if (b) b.disabled = true; });
  if (note) {
    note.classList.remove("hidden");
    note.textContent = `正在批量识别 ${sources.length} 个 C 源…`;
  }
  const payload = {
    catalogs: ["gaia", "2mass", "wise"],
    radius_arcsec: 3.0,
    sources: sources.filter((s) => Number.isFinite(Number(s.ra)) && Number.isFinite(Number(s.dec)) && Number.isFinite(Number(s.mjd))).map((s) => ({
      source_id: String(s.source_id || ""),
      ra: Number(s.ra),
      dec: Number(s.dec),
      mjd: Number(s.mjd),
      sigma_arcsec: 0.5,
    })),
  };
  if (!payload.sources.length) {
    if (note) note.textContent = "C 源缺少 ra/dec/mjd，无法批量识别；列表保持不变。";
    if (btn) { btn.disabled = false; btn.textContent = "批量识别 C 源"; }
    return;
  }
  try {
    const r = await fetch("/api/identify/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      throw new Error((e && (e.message || e.code)) || "批量识别接口返回错误");
    }
    const byId = {};
    (d.results || []).forEach((res) => { byId[String(res.source_id || res.detection_id || "")] = res; });
    state.anyfieldBatch = {
      byId,
      counts: d.counts || null,
      catalogStatus: d.catalog_status || null,
      error: null,
    };
    if (note) {
      note.classList.remove("hidden");
      note.innerHTML = `<b>${esc(batchSummary(d.counts))}</b> · gaia+2mass+wise · catalog ${esc(d.catalog_status || "?")} · 点击行仍做单源识别`;
    }
    renderCSources({ status: "ok", sources: state.anyfieldSources, truncated: false }, 200);
  } catch (e) {
    // 失败不覆盖旧列表/旧 badge
    if (note) {
      note.classList.remove("hidden");
      note.innerHTML = `批量识别失败：${esc(e.message)}。C 源列表和已有结果保持不变。`;
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "批量识别 C 源"; }
    ["#exportCSourcesBtn", "#exportCJsonBtn"].forEach((sel) => { const b = $(sel); if (b) b.disabled = false; });
  }
}

function renderCSources(data, httpStatus) {
  const box = $("#anyfieldSources");
  if (!box) return;
  box.classList.remove("hidden");
  const status = data && data.status;
  if (httpStatus === 200 && status === "ok") {
    const all = Array.isArray(data.sources) ? data.sources : [];
    state.anyfieldSources = all;
    if (!all.length) {
      const batchBtn = $("#batchCSourcesBtn");
      if (batchBtn) batchBtn.classList.add("hidden");
      const batchGroup = $("#anyfieldBatchActions");
      if (batchGroup) batchGroup.classList.add("hidden");
      box.innerHTML = '<div class="anyfield-source-note">C 源列表为空：该场尚未 ingest，或源在搜索半径外。空列表不等于“没有天体”。</div>';
      return;
    }
    const shown = all.slice(0, 50);
    const batch = state.anyfieldBatch || {};
    const summary = batch.counts ? ` · ${batchSummary(batch.counts)}` : "";
    const head = `<div class="anyfield-source-head">C 源列表：${all.length} 个（显示前 ${shown.length}${data.truncated ? "，已截断" : ""}）${esc(summary)}</div>`;
    const rows = shown.map((src, i) => {
      const q = (src.quality || []).join(" · ") || (src.saturated ? "SATURATED" : "—");
      const sid = esc(src.source_id || `#${i}`);
      const result = batch.byId && (batch.byId[String(src.source_id || "")] || batch.byId[String(i)]);
      return `<button type="button" class="anyfield-source${state.locked && state.locked.c_source && state.locked.source_id === src.source_id ? " locked" : ""}" data-c-index="${i}" title="${sid} · ${esc(q)}">
        <span>S/N ${Number(src.snr || 0).toFixed(1)}</span>
        <span class="c-coord">${Number(src.ra).toFixed(4)} ${Number(src.dec).toFixed(4)}</span>
        ${batchBadgeFor(result)}
      </button>`;
    }).join("");
    box.innerHTML = head + rows + '<div class="anyfield-source-note">点击源 → 锁定并调用 /api/identify。批量 badge 只是星表身份，不是发现声明。</div>';
    const batchBtn = $("#batchCSourcesBtn");
    if (batchBtn) batchBtn.classList.remove("hidden");
    const batchGroup = $("#anyfieldBatchActions");
    if (batchGroup) batchGroup.classList.remove("hidden");
    ["#exportCSourcesBtn", "#exportCJsonBtn"].forEach((sel) => {
      const b = $(sel);
      if (b) { b.classList.toggle("hidden", !batch.counts); b.disabled = false; }
    });
    box.querySelectorAll(".anyfield-source").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = Number(el.dataset.cIndex);
        if (Number.isInteger(idx) && state.anyfieldSources[idx]) lockCSource(state.anyfieldSources[idx]);
      });
    });
  } else if (status === "no_coverage" || httpStatus === 422) {
    const bb = $("#batchCSourcesBtn"); if (bb) bb.classList.add("hidden");
    const bg = $("#anyfieldBatchActions"); if (bg) bg.classList.add("hidden");
    box.innerHTML = '<div class="anyfield-source-note">C 确认无覆盖，没有可加载的源列表。</div>';
  } else if (status === "index_incomplete" || httpStatus === 503) {
    const bb = $("#batchCSourcesBtn"); if (bb) bb.classList.add("hidden");
    const bg = $("#anyfieldBatchActions"); if (bg) bg.classList.add("hidden");
    box.innerHTML = '<div class="anyfield-source-note">C 源列表不可用（索引不完整）；旧分析和本地源列表不受影响。</div>';
  } else {
    const bb = $("#batchCSourcesBtn"); if (bb) bb.classList.add("hidden");
    const bg = $("#anyfieldBatchActions"); if (bg) bg.classList.add("hidden");
    box.innerHTML = '<div class="anyfield-source-note">C 源列表不可用；旧分析和本地源列表不受影响。</div>';
  }
}

function lockCSource(src) {
  if (!src) return;
  const ra = Number(src.ra);
  const dec = Number(src.dec);
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
  const sigma = 0.5; // C-P1A 未给质心误差；按 0.5″ 保守处理，结果会带 caveat。
  state.locked = {
    ra,
    dec,
    mjd: Number(src.mjd),
    snr: Number(src.snr) || 0,
    flux: Number(src.flux) || 0,
    flux_err: Number(src.flux_err) || 0,
    quality: Array.isArray(src.quality) ? src.quality : [],
    saturated: !!src.saturated,
    epoch_id: src.epoch_id || null,
    frame: 0,
    c_source: true,
    source_id: String(src.source_id),
    sigma_arcsec: sigma,
  };
  state.lockedIdentification = null;
  state.lockedIdentificationKey = null;
  state.selected = "custom";
  $("#ra").value = ra.toFixed(6);
  $("#dec").value = dec.toFixed(6);
  renderPresets();
  hideTip();
  const u = new URL(location.href);
  u.searchParams.set("target", "custom");
  u.searchParams.set("ra", ra.toFixed(6));
  u.searchParams.set("dec", dec.toFixed(6));
  u.searchParams.set("fov", $("#fov").value);
  u.searchParams.set("lock", "1");
  history.replaceState(null, "", u.toString());
  note(`已锁定 C 源 ${esc(src.source_id || "")}：RA ${ra.toFixed(6)}° · Dec ${dec.toFixed(6)}°；已发起星表识别…`);
  renderLockInfo();
  renderCSources({ status: "ok", sources: state.anyfieldSources, truncated: false }, 200);
  identifyLockedSource(state.locked);
}

function fmtArcsec(value, digits = 3) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(digits)}″`;
}

/* ------------------------------------------------------------------ 启动 */

async function init() {
  bindUI();
  try {
    const r = await fetch("/api/targets");
    const d = await r.json();
    state.targets = d.targets || [];
    renderPresets();
  } catch (e) {
    note("无法读取目标列表：" + e.message);
  }
  const params = new URLSearchParams(location.search);
  const t0Param = params.get("t0");
  const t1Param = params.get("t1");
  if (t0Param) $("#t0").value = t0Param;
  if (t1Param) $("#t1").value = t1Param;
  state.fromView = params.get("from") === "view";
  const layerParam = params.get("mode");
  if (["browse", "cascade", "tracks"].includes(layerParam)) state.layerMode = layerParam;
  setLayerModeUI(state.layerMode);
  if (params.get("ra") && params.get("dec")) {
    $("#ra").value = params.get("ra");
    $("#dec").value = params.get("dec");
    if (params.get("fov")) $("#fov").value = params.get("fov");
    if (params.get("lock") === "1") {
      // 点击源后写入的 URL：分析完成后按 RA/Dec 重新找到该源并高亮
      state.lockRequest = { ra: parseFloat(params.get("ra")), dec: parseFloat(params.get("dec")) };
    }
    const tid = params.get("target");
    const preset = tid ? state.targets.find((t) => t.id === tid) : null;
    const same = preset && Math.abs(preset.ra - parseFloat(params.get("ra"))) < 1e-6 && Math.abs(preset.dec - parseFloat(params.get("dec"))) < 1e-6;
    state.selected = same ? tid : "custom";
    renderPresets();
    analyze();
  } else {
    selectPreset(params.get("target") || "barnard", false);
  }
}

function bindUI() {
  $("#runBtn").addEventListener("click", analyze);
  $("#play").addEventListener("click", togglePlay);
  $("#slider").addEventListener("input", (e) => {
    state.frame = +e.target.value;
    renderFrame();
  });
  $$(".modes button").forEach((b) =>
    b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      $$(".modes button").forEach((x) => x.classList.toggle("active", x === b));
      renderFrame();
    })
  );
  $$(".layer-tabs button").forEach((b) => b.addEventListener("click", () => setLayerMode(b.dataset.layerMode)));
  $("#expert").addEventListener("change", (e) => document.body.classList.toggle("expert", e.target.checked));
  $("#theoryBtn").addEventListener("click", () => $("#theoryModal").classList.remove("hidden"));
  $("#closeTheory").addEventListener("click", () => $("#theoryModal").classList.add("hidden"));
  $("#theoryModal").addEventListener("click", (e) => {
    if (e.target.id === "theoryModal") $("#theoryModal").classList.add("hidden");
  });
  $("#download").addEventListener("click", downloadJSON);
  $("#copyLink").addEventListener("click", copyLink);
  const sky = $("#sky");
  sky.addEventListener("mousemove", onCanvasMove);
  sky.addEventListener("mouseleave", onCanvasLeave);
  sky.addEventListener("click", onCanvasClick);
  $("#lockInfo").addEventListener("click", (e) => {
    if (e.target && e.target.id === "unlockBtn") clearLock();
  });
  const loadBtn = $("#loadCSourcesBtn");
  if (loadBtn) loadBtn.addEventListener("click", loadCSources);
  const aBatchBtn = $("#batchASourcesBtn");
  if (aBatchBtn) aBatchBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); batchIdentifyASources(); });
  const exportABtn = $("#exportASourcesBtn");
  if (exportABtn) exportABtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); exportBatchServer("a", "csv"); });
  const exportAJson = $("#exportAJsonBtn");
  if (exportAJson) exportAJson.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); exportBatchServer("a", "json"); });
  const exportCBtn = $("#exportCSourcesBtn");
  if (exportCBtn) exportCBtn.addEventListener("click", () => exportBatchServer("c", "csv"));
  const exportCJson = $("#exportCJsonBtn");
  if (exportCJson) exportCJson.addEventListener("click", () => exportBatchServer("c", "json"));
  const dirBtn = $("#candidateDirectoryBtn");
  if (dirBtn) dirBtn.addEventListener("click", (e) => { e.preventDefault(); openCandidateDirectory(); });
  const dirCsv = $("#exportDirectoryCsvBtn");
  if (dirCsv) dirCsv.addEventListener("click", () => exportCandidateDirectory("csv"));
  const dirJson = $("#exportDirectoryJsonBtn");
  if (dirJson) dirJson.addEventListener("click", () => exportCandidateDirectory("json"));
  const batchBtn = $("#batchCSourcesBtn");
  if (batchBtn) batchBtn.addEventListener("click", batchIdentifyCSources);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("#theoryModal").classList.add("hidden");
    if (e.key === " " && state.analysis) {
      e.preventDefault();
      togglePlay();
    }
  });
  window.addEventListener("resize", debounce(() => state.analysis && renderFrame(), 150));
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

/* ------------------------------------------------------------------ 目标 */

function renderPresets() {
  const box = $("#presets");
  box.innerHTML = "";
  state.targets.forEach((t) => {
    const b = document.createElement("button");
    b.className = "preset" + (t.official ? " official" : "") + (state.selected === t.id ? " active" : "");
    b.innerHTML = `<b>${t.name_zh || t.name}</b><span>${Number(t.ra).toFixed(2)} ${Number(t.dec) >= 0 ? "+" : ""}${Number(t.dec).toFixed(2)}</span>`;
    b.addEventListener("click", () => selectPreset(t.id, true));
    box.appendChild(b);
  });
  const c = document.createElement("button");
  c.className = "preset" + (state.selected === "custom" ? " active" : "");
  c.innerHTML = `<b>自定义天区</b><span>RA / Dec / FOV</span>`;
  c.addEventListener("click", () => {
    state.selected = "custom";
    renderPresets();
    note("输入任意坐标后点击“识别运动规律”。");
  });
  box.appendChild(c);
}

function selectPreset(id, run) {
  const t = state.targets.find((x) => x.id === id) || state.targets[0];
  if (!t) return;
  clearLock(false);
  state.selected = t.id;
  $("#ra").value = t.ra;
  $("#dec").value = t.dec;
  if (t.id === "epsind_b") $("#fov").value = 0.25;
  else if (t.id === "barnard") $("#fov").value = 0.2;
  else $("#fov").value = 0.2;
  renderPresets();
  note(`已选择 <b>${t.name}</b>。类型 ${t.type}；预期规律 ${t.expected}。`);
  if (run) analyze();
}

function note(html, cls = "") {
  const n = $("#sourceNote");
  n.innerHTML = html;
  n.className = "note " + cls;
}

/* ------------------------------------------------------------------ 分析 */

async function analyze() {
  if (state.loading) return;
  const ra = parseFloat($("#ra").value);
  const dec = parseFloat($("#dec").value);
  const preset = state.targets.find((t) => t.id === state.selected);
  // 用户手动改过坐标就按自定义天区处理，避免拿 A 天区的历元去分析 B 坐标
  const sameAsPreset = preset && Math.abs(preset.ra - ra) < 1e-6 && Math.abs(preset.dec - dec) < 1e-6;
  const fov = parseFloat($("#fov").value);
  if (!Number.isFinite(fov) || fov < 0.02 || fov > 3.0) {
    note("视场 FOV 需在 <b>0.02–3.0 度</b>之间（当前 " + $("#fov").value + "）。", "err");
    return;
  }
  const win = readTimeWindow();
  if (!win.ok) {
    note(win.error, "err");
    return;
  }
  const payload = {
    target_id: preset && sameAsPreset ? preset.id : "custom",
    ra,
    dec,
    fov,
    mode: $("#mode").value,
  };
  if (win.t0 != null) payload.t0 = win.t0;
  if (win.t1 != null) payload.t1 = win.t1;
  if (!Number.isFinite(payload.ra) || !Number.isFinite(payload.dec)) {
    note("请输入有效的 RA / Dec（度）。", "err");
    return;
  }
  if (payload.mode === "sample") hideAnyfield();
  else refreshAnyfield(ra, dec, fov);
  state.loading = true;
  $("#runBtn").disabled = true;
  $("#runBtn").querySelector(".dot").style.animation = "spin 1s linear infinite";
  $("#canvasLoading").classList.remove("hidden");
  $("#canvasEmpty").classList.add("hidden");
  stopPlay();
  try {
    const t0 = performance.now();
    const r = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok || d.error) {
      const e = d.error;
      const msg = typeof e === "string" ? e : (e && (e.message || e.code)) || "服务返回错误";
      const code = e && e.code ? `[${e.code}] ` : "";
      throw new Error(code + msg);
    }
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    state.analysis = d;
    state.analysisFov = fov;
    state.frame = 0;
    state.hover = null;
    state.hoverKey = null;
    state.cascadeData = null;
    state.tracksData = null;
    state.layerKeys = { cascade: null, tracks: null };
    state.selectedTrack = null;
    state.aSourceBatch = { byId: {}, counts: null, error: null };
    state.candidateDirectory = null;
    hideTip();
    await preload(d);
    renderAll(d, dt);
    syncURL();
  } catch (e) {
    console.error(e);
    note("分析失败：" + e.message, "err");
    $("#canvasEmpty").classList.remove("hidden");
  } finally {
    state.loading = false;
    $("#runBtn").disabled = false;
    $("#runBtn").querySelector(".dot").style.animation = "";
    $("#canvasLoading").classList.add("hidden");
  }
}

function loadImage(url) {
  return new Promise((resolve) => {
    const im = new Image();
    im.onload = () => resolve(im);
    im.onerror = () => resolve(null);
    im.src = url;
  });
}

async function preload(d) {
  if (!d.epochs || !d.epochs.length) {
    state.main = [];
    state.diff = [];
    state.stack = null;
    return;
  }
  state.main = await Promise.all(d.epochs.map((e) => loadImage(e.main_url)));
  state.diff = await Promise.all(d.epochs.map((e) => loadImage(e.diff_url)));
  state.stack = d.stack_url ? await loadImage(d.stack_url) : null;
}

/* ------------------------------------------------------------------ 渲染 UI */

function renderAll(d, dt) {
  updateTimeBadge(d.time_filter);
  if (!d.epochs || !d.epochs.length) {
    renderEmptyAnalysis(d);
    return;
  }
  const r = d.result;
  const cls = CLASS_META[r.class] || CLASS_META.U;
  const isSample = d.data.mode === "sample";
  state.sources = Array.isArray(d.sources) ? d.sources : [];
  resolveLock(d);

  // 顶部徽章
  const mb = $("#modeBadge");
  mb.className = "badge " + (d.data.mode || "synthetic");
  mb.textContent = { real: "REAL 真实", hybrid: "HYBRID 混合", synthetic: "SIM 模拟", sample: "SAMPLE" }[d.data.mode] || d.data.mode;
  const rb = $("#readyBadge");
  rb.className = "badge " + (r.readiness || "").toLowerCase();
  rb.textContent = (r.readiness || "?") + " 就绪度";

  const fb = d.data.fallback_reason ? ` · ${d.data.fallback_reason}` : "";
  // 三类数分开显示：取数期数 / 用于拟合的探测期数 / 探测基线（目录基线只在 data.note 里带限定词）
  const det = Number(r.detected_epochs != null ? r.detected_epochs : 0);
  const nEpoch = Number(d.data.n_epochs || 0);
  const probe = Number(r.baseline_days || 0);
  const fetchTxt = d.data.fetch_attempted
    ? `取数 ${Number(d.data.fetch_succeeded || 0)}/${d.data.fetch_attempted} 期 · `
    : "";
  const fitTxt = isSample ? "仅解析/渲染核验" : `拟合 ${det}/${nEpoch} 期`;
  const baseTxt = isSample ? "" : ` · 探测基线 ${Number.isFinite(probe) ? probe.toFixed(0) : "—"} 天`;
  // C 源锁定后的 note 由 /api/identify 负责；避免本次 analyze 完成时把
  // “已锁定 C 源 / 数据不足”覆盖回分析摘要。A 源不受影响。
  if (!(state.locked && state.locked.c_source)) {
    note(
      `<b>${(d.data.mode || "").toUpperCase()}</b> · ${nEpoch} 期 · ${(d.data.bands || []).join("/")} · ${fetchTxt}${fitTxt}${baseTxt}${fb} · ${dt}s<br>${d.data.note || ""}`
    );
  }

  // 就绪度
  $$(".ready-row").forEach((row) => {
    const lv = row.dataset.level;
    row.classList.toggle("on", lv <= r.readiness);
    row.classList.toggle("current", lv === r.readiness);
  });

  // 质量标记
  const q = $("#quality");
  q.innerHTML = "";
  (r.quality || []).forEach((f) => {
    const el = document.createElement("span");
    el.className = "flag";
    el.textContent = f;
    q.appendChild(el);
  });

  // 鉴定卡
  const priorMotion = r.catalog_motion && r.catalog_motion.basis === "catalog_prior";
  const v = $("#verdict");
  v.className = "verdict";
  v.dataset.class = r.class;
  v.innerHTML = `
    <div class="eyebrow">MOTION FINGERPRINT · ${r.readiness}${priorMotion ? " · CATALOG PRIOR" : ""}</div>
    <div class="verdict-main">
      <div class="class-dot">${cls.letter}</div>
      <div>
        <h3>${isSample ? "样本核验（不做测量）" : priorMotion ? "L · Gaia 先验" : (r.label_zh || cls.label)}</h3>
        <p>${isSample ? "两张图来自不同天区 · 仅验证解析与渲染" : priorMotion ? "SPHEREx 本次未独立测出；采用 Gaia PM 先验；不是发现" : cls.sub + (r.moving ? " · 位移显著" : "") + (r.variable ? " · 变光显著" : "")}</p>
      </div>
    </div>
    <div class="conf">
      <div class="bar"><i style="width:${Math.round((r.confidence || 0) * 100)}%"></i></div>
      <span>置信度 ${(r.confidence * 100).toFixed(0)}%</span>
    </div>`;

  const pt = $("#publicText");
  pt.classList.remove("hidden");
  pt.textContent = r.public_text;

  renderMetrics(r);
  renderIdentification(d.identification);
  renderModels(r);
  renderEpochList(d);
  renderMatchStats(r);
  renderEvidence(r);
  renderTruth(d);
  drawCharts(d);

  $("#download").disabled = false;
  $("#copyLink").disabled = false;
  $("#strip").innerHTML = "";
  d.epochs.forEach((ep, i) => {
    const b = document.createElement("button");
    b.className = "strip-thumb";
    b.innerHTML = `<img src="${ep.main_url}" alt="epoch ${i}">`;
    b.addEventListener("click", () => {
      state.frame = i;
      renderFrame();
    });
    $("#strip").appendChild(b);
  });
  $("#slider").max = Math.max(d.epochs.length - 1, 0);
  $("#canvasEmpty").classList.add("hidden");
  renderASources(d);
  renderFrame();
  renderLayerPanel();
  if (state.layerMode !== "browse") loadLayerData(state.layerMode);
}

function renderMetrics(r) {
  const fp = r.fingerprint;
  const moving = r.moving;
  const prior = r.catalog_motion && r.catalog_motion.basis === "catalog_prior" ? r.catalog_motion : null;
  const mu = prior
    ? `${(prior.pm_mas_yr / 1000).toFixed(2)} <small>Gaia 先验</small>`
    : moving
      ? `${(fp.mu_mas_yr / 1000).toFixed(2)} <small>″/yr</small>`
      : `&lt; ${((fp.mu_err_mas_yr || 0) * 3 / 1000).toFixed(2)} <small>3σ</small>`;
  const pa = prior
    ? "—"
    : moving && fp.pa_deg != null ? `${fp.pa_deg.toFixed(0)}°` : "—";
  const amp = fp.amp_mag > 0.25 ? `${fp.amp_mag.toFixed(2)} <small>mag</small>` : "—";
  const period = fp.delta_bic_var > 6 && fp.period_d ? `${fp.period_d.toFixed(1)} <small>d</small>` : "—";
  const acc = fp.accel_sig > 2 ? `${fp.accel_sig.toFixed(1)}σ` : "—";
  const lin = moving && Number.isFinite(fp.linearity) ? `${(fp.linearity * 100).toFixed(1)}%` : "—";

  const cards = [
    [prior ? "Gaia 先验 μ" : "自行 μ", mu, moving || !!prior],
    ["方向 PA", pa, false],
    ...(prior ? [["SPHEREx 实测 μ", `${(fp.mu_mas_yr / 1000).toFixed(2)} <small>±${(fp.mu_err_mas_yr / 1000).toFixed(2)}″/yr</small>`, false]] : []),
    ["首末位移", `${fp.displacement_arcsec.toFixed(2)} <small>±${(fp.displacement_err_arcsec || 0).toFixed(2)}″</small>`, moving],
    ["亮度振幅", amp, fp.amp_mag > 0.25],
    ["周期候选", period, !!fp.period_d],
    ["加速显著性", acc, fp.accel_sig > 2],
    ["轨迹非直线度", lin, false],
    ["有效历元", `${fp.n_epochs} <small>期</small>`, false],
  ];
  $("#metrics").classList.remove("hidden");
  $("#metrics").innerHTML = cards
    .map(([k, v, hl]) => `<div class="metric ${hl ? "hl" : ""}"><span>${k}</span><b>${v}</b></div>`)
    .join("");
}

function renderIdentification(ident) {
  const card = $("#catalogCard");
  const badge = $("#catalogBadge");
  const body = $("#catalogBody");
  const disclaimer = $("#catalogDisclaimer");
  if (!card || !badge || !body) return;
  if (!ident || !ident.status) {
    card.classList.add("hidden");
    return;
  }
  const status = ident.status;
  card.classList.remove("hidden");
  card.dataset.status = status;
  badge.className = "catalog-badge " + status;
  badge.textContent = catalogText(ident);

  const n = Number(ident.n_candidates || 0);
  const releaseName = ident.catalog_release || ident.release;
  const release = releaseName ? `<span>${esc(releaseName)}</span>` : "";
  const quality = (ident.quality || []).filter(Boolean);
  let html = "";

  if (status === "known_star") {
    const pm = ident.pm_consistent === true ? "一致" : ident.pm_consistent === false ? "不一致" : "未判";
    const obj = ident.object_type ? `<span class="catalog-chip">${esc(ident.object_type)}</span>` : "";
    const ruwe = ident.ruwe != null ? `<span class="catalog-chip">RUWE ${Number(ident.ruwe).toFixed(2)}</span>` : "";
    const photo = ident.photometry || {};
    const colors = photo.colors || {};
    const ir = [
      colors.j_h != null ? `J−H ${Number(colors.j_h).toFixed(2)}` : "",
      colors.w1_w2 != null ? `W1−W2 ${Number(colors.w1_w2).toFixed(2)}` : "",
      photo.twomass_id ? "2MASS" : "",
      photo.wise_id ? "WISE" : "",
    ].filter(Boolean).map((x) => `<span class="catalog-chip">${esc(x)}</span>`).join("");
    const pv = ident.phot_variable_flag && String(ident.phot_variable_flag).toUpperCase() !== "NOT_AVAILABLE"
      ? `<span class="catalog-chip">${esc(ident.phot_variable_flag)}</span>` : "";
    const cross = ((ident.candidates || [])[0] || {}).crossmatches || [];
    const crossChips = cross.map((c) => {
      const name = String(c.catalog || "catalog").toUpperCase();
      const id = c.catalog_id || "—";
      const mags = [c.jmag != null ? `J ${Number(c.jmag).toFixed(2)}` : "",
                    c.w1 != null ? `W1 ${Number(c.w1).toFixed(2)}` : ""].filter(Boolean).join(" ");
      return `<span class="catalog-chip" title="${esc(name)} ${esc(id)}">${esc(name)} ${esc(id)}${mags ? " · " + esc(mags) : ""}</span>`;
    }).join("");
    html = `
      <div class="catalog-id">${esc(ident.source_id || ident.catalog_id || "—")}${release}</div>
      <div class="catalog-metrics">
        <span class="catalog-chip">角距 ${fmtArcsec(ident.separation_arcsec)}</span>
        <span class="catalog-chip">自行 ${pm}</span>
        <span class="catalog-chip">候选 ${n}</span>
        ${obj}${ruwe}${ir}${pv}${crossChips}
        ${quality.map((q) => `<span class="catalog-chip">${esc(q)}</span>`).join("")}
      </div>`;
  } else if (status === "ambiguous") {
    const cands = (ident.candidates || []).slice(0, 5);
    const rows = cands
      .map((c) => {
        const cross = (c.crossmatches || []).map((x) => `${String(x.catalog || "").toUpperCase()} ${x.catalog_id || ""}`).join(" · ");
        return `<div class="catalog-candidate"><code>${esc(c.catalog_id || "—")}</code><span>${fmtArcsec(c.separation_arcsec)}</span><em>${esc(c.release || ident.release || "")}</em>${cross ? `<span class="catalog-chip">${esc(cross)}</span>` : ""}</div>`;
      })
      .join("");
    html = `
      <div>匹配半径内有 <b>${n || cands.length}</b> 个星表源；系统<b>不自动选择</b>最亮或最近的候选。</div>
      <div class="catalog-candidates">${rows || '<span class="muted">候选明细缺失</span>'}</div>
      <div class="catalog-note">${esc(catalogReasonText(ident))}。请人工核对候选；ambiguous 不是“已确认”。</div>`;
  } else if (status === "uncatalogued_candidate") {
    html = `
      <div>星表查询成功，<b>0 个候选</b>。这只是“未收录候选（未经确认）”，不是发现。</div>
      <div class="catalog-note">可能是星表遗漏、星系/QSO、伪源或 6.15″/px 下的混合；未匹配不等于新天体。</div>`;
  } else if (status === "catalog_unavailable") {
    html = `
      <div><b>星表不可用，无法判断已知/未知。</b></div>
      <div class="catalog-note">${esc(catalogReasonText(ident))}；本次运动分析结果不受影响，也未把空结果当成“未收录”。</div>`;
  } else {
    html = `
      <div>暂不能给出星表结论：<b>${esc(catalogReasonText(ident))}</b>。</div>
      <div class="catalog-note">需要目标实测位置与可用的星表查询；这里不猜测，也不输出“新天体”。</div>`;
  }
  body.innerHTML = html;
  disclaimer.textContent = ident.disclaimer || "未匹配不等于新天体；本工具只做已知/未知比对，不做发现声明。";
}

function renderModels(r) {
  const wrap = $("#modelsWrap");
  const box = $("#models");
  box.innerHTML = "";
  const models = (r.models || []).slice(0, 5);
  if (!models.length) {
    box.innerHTML = `<p class="muted" style="font-size:11px">历元不足，未做模型比较。</p>`;
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  const best = models[0].id;
  models.forEach((m) => {
    const row = document.createElement("div");
    row.className = "model-row" + (m.id === best ? " best" : "");
    row.innerHTML = `<span>${m.id}</span><div class="bar"><i style="width:${Math.max(2, m.prob * 100)}%"></i></div><em>${(m.prob * 100).toFixed(0)}%</em>`;
    box.appendChild(row);
  });
}

function renderEpochList(d) {
  const box = $("#epochList");
  if (!box) return;
  box.innerHTML = "";
  (d.epochs || []).forEach((ep, i) => {
    const row = document.createElement("button");
    row.className = "epoch-row";
    const date = (ep.date_obs || "").slice(0, 10) || "日期缺失";
    row.innerHTML = `<em>${date}</em><span>${ep.band || "?"}</span><span>${ep.obsid || "—"}</span>`;
    row.title = `${ep.provenance || ""} · MJD ${Number(ep.mjd || 0).toFixed(2)}`;
    row.addEventListener("click", () => {
      state.frame = i;
      renderFrame();
    });
    box.appendChild(row);
  });
}

function renderMatchStats(r) {
  const box = $("#matchStats");
  if (!box) return;
  const stats = r.match_stats || [];
  if (!stats.length) {
    box.innerHTML = `<p class="muted">无参考星定标数据（单期或样本模式）。</p>`;
    return;
  }
  const rows = stats
    .map((m) => `<span class="match-chip">第 ${Number(m.epoch) + 1} 期 · ${m.n_match} 星 · RMS ${m.rms_px == null ? "—" : Number(m.rms_px).toFixed(2) + " px"}</span>`)
    .join("");
  box.innerHTML = `<div class="match-title">参考星相对定标</div><div class="match-list">${rows}</div>`;
}

function renderEvidence(r) {
  $("#evidenceWrap").classList.remove("hidden");
  $("#evidence").innerHTML = (r.evidence || []).map((e) => `<li>${e}</li>`).join("");
  $("#falsifier").textContent = r.falsifier || "";
  const cav = $("#caveats");
  const list = r.caveats || [];
  if (list.length) {
    cav.classList.remove("hidden");
    cav.innerHTML = `<b>注意</b>${list.map((c) => `<p>${c}</p>`).join("")}`;
  } else {
    cav.classList.add("hidden");
  }
}

function renderTruth(d) {
  const box = $("#truthBox");
  const t = d.truth;
  if (!t || t.mu_mas_yr == null) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  const ratio = (d.result.mu_mas_yr / t.mu_mas_yr) * 100;
  box.innerHTML = `<b>注入真值</b> μ=${(t.mu_mas_yr / 1000).toFixed(2)}″/yr · PA=${Number(t.pa_deg).toFixed(1)}°<br>
    <b>回收</b> μ=${(d.result.mu_mas_yr / 1000).toFixed(2)}″/yr（${ratio.toFixed(0)}%）· PA=${Number(d.result.pa_deg || 0).toFixed(1)}°`;
}


/* ------------------------------------------------------------------ 主界面：时间窗口 / 模式栏 / 分析层 */

function readTimeWindow() {
  const t0 = $("#t0") ? $("#t0").value.trim() : "";
  const t1 = $("#t1") ? $("#t1").value.trim() : "";
  if (!t0 && !t1) return { ok: true, t0: null, t1: null };
  if (!t0 || !t1) return { ok: false, error: "t0 与 t1 必须同时填写（MJD）。" };
  const a = Number(t0), b = Number(t1);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return { ok: false, error: "t0/t1 必须是 MJD 数字。" };
  if (a > b) return { ok: false, error: "t0 必须 ≤ t1。" };
  return { ok: true, t0: a, t1: b };
}

function analysisTimeParams() {
  const tf = state.analysis && state.analysis.time_filter;
  if (tf && tf.filtered) return { ok: true, t0: Number(tf.t0), t1: Number(tf.t1) };
  return readTimeWindow();
}

function analysisFov() {
  return Number.isFinite(Number(state.analysisFov)) ? Number(state.analysisFov) : parseFloat($("#fov").value);
}

function timeFilterText(tf) {
  if (!tf || !tf.filtered) return tf && tf.requested != null ? `全时段 ${tf.requested} 期` : "全时段";
  const prefix = state.fromView ? "来自视图 · " : "";
  return `${prefix}时间窗口 MJD ${Number(tf.t0).toFixed(3)}–${Number(tf.t1).toFixed(3)} · 保留 ${tf.kept}/${tf.requested} 期`;
}

function updateTimeBadge(tf) {
  const el = $("#timeBadge");
  if (!el) return;
  if (!tf || !tf.filtered) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.textContent = timeFilterText(tf);
  el.title = `${timeFilterText(tf)}${tf.kept < 2 ? " · insufficient" : ""}`;
}

function layerDetailURL(mode) {
  const d = state.analysis;
  if (!d || !d.target) return mode === "tracks" ? "/tracks" : "/cascade";
  const q = new URLSearchParams();
  q.set("ra", Number(d.target.ra).toFixed(6));
  q.set("dec", Number(d.target.dec).toFixed(6));
  q.set("fov", String(analysisFov() || 0.2));
  const win = analysisTimeParams();
  if (win.ok && win.t0 != null) {
    q.set("t0", String(win.t0));
    q.set("t1", String(win.t1));
  }
  return (mode === "tracks" ? "/tracks" : "/cascade") + "?" + q.toString();
}

function setLayerModeUI(mode) {
  $$(".layer-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.layerMode === mode));
  const link = $("#detailLink");
  if (link) {
    link.classList.toggle("hidden", mode === "browse");
    link.href = layerDetailURL(mode);
    link.textContent = mode === "tracks" ? "轨迹详细表格 ↗" : "级联详细表格 ↗";
  }
  const panel = $("#layerPanel");
  if (panel && mode === "browse") panel.classList.add("hidden");
}

function setLayerMode(mode) {
  if (!["browse", "cascade", "tracks"].includes(mode)) return;
  state.layerMode = mode;
  setLayerModeUI(mode);
  if (state.analysis) {
    if (mode === "browse") renderLayerPanel();
    else loadLayerData(mode);
    renderFrame();
  }
  syncURL();
}

const LAYER_API = { cascade: "/api/cascade", tracks: "/api/tracks" };

function layerQueryString() {
  const d = state.analysis;
  if (!d || !d.target) return null;
  const q = new URLSearchParams();
  if (d.analysis_id) q.set("analysis_id", d.analysis_id);
  q.set("ra", Number(d.target.ra));
  q.set("dec", Number(d.target.dec));
  q.set("fov", String(analysisFov() || 0.2));
  const win = analysisTimeParams();
  if (win.ok && win.t0 != null) {
    q.set("t0", String(win.t0));
    q.set("t1", String(win.t1));
  }
  if (state.fromView) q.set("from", "view");
  return q.toString();
}

async function loadLayerData(mode) {
  const d = state.analysis;
  if (!d || mode === "browse") return;
  if (!d.epochs || !d.epochs.length) {
    renderLayerError("时间窗口内没有历元；没有可叠加的分析层。");
    return;
  }
  const q = layerQueryString();
  if (!q) return;
  const key = `${mode}|${q}`;
  if (state.layerKeys[mode] === key) {
    renderLayerPanel();
    renderFrame();
    return;
  }
  state.layerLoading = true;
  renderLayerPanel();
  try {
    const r = await fetch(`${LAYER_API[mode]}?${q}`);
    const data = await r.json();
    if (!r.ok || data.error) {
      const e = data.error;
      throw new Error((e && (e.message || e.code)) || `${mode} API 返回错误`);
    }
    if (mode === "cascade") state.cascadeData = data;
    else state.tracksData = data;
    state.layerKeys[mode] = key;
    state.layerLoading = false;
    renderLayerPanel();
    renderFrame();
  } catch (e) {
    state.layerLoading = false;
    renderLayerError(`${e.message}。没有伪造层数据。`);
  }
}

function currentCascadeEpoch() {
  const data = state.cascadeData;
  const d = state.analysis;
  if (!data || !d || !d.epochs || !d.epochs.length) return null;
  const ep = d.epochs[currentFrameIndex()];
  const id = ep && ep.id;
  return (data.epochs || []).find((e) => e.epoch_id === id) || (data.epochs || [])[currentFrameIndex()] || null;
}

function renderLayerError(msg) {
  const panel = $("#layerPanel");
  if (!panel) return;
  panel.classList.remove("hidden");
  $("#layerTitle").textContent = state.layerMode === "tracks" ? "轨迹" : "级联/残差";
  $("#layerBadge").className = "badge rose";
  $("#layerBadge").textContent = "不可用";
  $("#layerStats").innerHTML = "";
  $("#layerToggles").innerHTML = "";
  $("#layerDetail").innerHTML = `<div class="layer-tip">${esc(msg)}</div>`;
  $("#layerDisclaimer").textContent = "候选不是新天体；不做发现声明。";
}

function renderLayerPanel() {
  const panel = $("#layerPanel");
  if (!panel) return;
  if (state.layerMode === "browse") {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const isTracks = state.layerMode === "tracks";
  $("#layerTitle").textContent = isTracks ? "轨迹 · D0–D4" : "级联/残差 · 当前帧";
  $("#layerDisclaimer").textContent = isTracks
    ? "轨迹候选不是新天体；D5 才需要人工/后续确认。"
    : "residual 候选不是新天体；未匹配不等于新星。";
  const badge = $("#layerBadge");
  if (state.layerLoading) {
    badge.className = "badge ghost";
    badge.textContent = "加载中";
    $("#layerStats").innerHTML = "";
    $("#layerToggles").innerHTML = "";
    $("#layerDetail").innerHTML = '<div class="layer-tip">读取分析层；不重跑 /api/analyze。</div>';
    return;
  }
  if (isTracks) {
    const data = state.tracksData;
    if (!data) {
      renderLayerError("轨迹数据尚未加载。");
      return;
    }
    const c = data.counts || {};
    badge.className = "badge " + (data.insufficient ? "rose" : "ghost");
    badge.textContent = data.insufficient ? "insufficient" : "候选";
    $("#layerStats").innerHTML = ["D0", "D1", "D2", "D3", "D4"].map((lv) =>
      `<span class="layer-stat ${lv}">${lv} ${c[lv] ?? 0}</span>`
    ).join("") + `<span class="layer-stat">共 ${(data.tracks || []).length}</span>`;
    $("#layerToggles").innerHTML = '<span class="layer-tip">轨迹线按 D0–D4 配色；点击轨迹/节点查看详情。</span>';
    const t = state.selectedTrack;
    if (t) {
      $("#layerDetail").innerHTML = `<b>${esc(t.track_id)}</b> · ${esc(t.level)} · ${esc(t.model)} · ${t.n_epochs} 期<br>
        RA ${Number(t.ra).toFixed(5)}° · Dec ${Number(t.dec).toFixed(5)}°<br>
        μ ${t.mu_arcsec_yr == null ? "—" : Number(t.mu_arcsec_yr).toFixed(2) + "″/yr"} · PA ${t.pa_deg == null ? "—" : Number(t.pa_deg).toFixed(1) + "°"} · catalog ${esc(t.catalog || "none")}<br>
        <span class="layer-tip">${(t.caveats || []).map(esc).join(" · ") || "—"}</span>`;
    } else {
      $("#layerDetail").innerHTML = '<div class="layer-tip">点击轨迹线选中；点击节点跳帧并调用 /api/identify。</div>';
    }
  } else {
    const data = state.cascadeData;
    if (!data) {
      renderLayerError("级联数据尚未加载。");
      return;
    }
    const row = currentCascadeEpoch();
    const counts = row ? row.counts : { l1: 0, masked: 0, residual: 0 };
    badge.className = "badge ghost";
    badge.textContent = row ? `当前帧 ${counts.l1}/${counts.masked}/${counts.residual}` : "无当前帧";
    $("#layerStats").innerHTML = `<span class="layer-stat">L1 ${counts.l1}</span>
      <span class="layer-stat">masked ${counts.masked}</span>
      <span class="layer-stat">residual ${counts.residual}</span>
      <span class="layer-stat">窗口 ${timeFilterText(data.time_filter)}</span>`;
    $("#layerToggles").innerHTML = `
      <label><input type="checkbox" id="lyrL1" ${state.layerVis.l1 ? "checked" : ""}> L1 实心</label>
      <label><input type="checkbox" id="lyrMasked" ${state.layerVis.masked ? "checked" : ""}> masked 灰叉</label>
      <label><input type="checkbox" id="lyrResidual" ${state.layerVis.residual ? "checked" : ""}> residual 空心</label>`;
    ["l1", "masked", "residual"].forEach((k) => {
      const el = document.getElementById("lyr" + k[0].toUpperCase() + k.slice(1));
      if (el) el.addEventListener("change", () => { state.layerVis[k] = el.checked; renderFrame(); });
    });
    const locked = state.locked && (state.locked.cascade_source || state.locked.track_source) ? state.locked : null;
    const ident = locked ? lockedIdentification() : null;
    let detail = '<div class="layer-tip">L1 实心 · masked 灰叉/灰圈 · residual 空心琥珀；点击 residual 锁定并 /api/identify。</div>';
    if (locked) {
      detail += `<div class="layer-detail"><b>已选 ${esc(locked.layer || "节点")}</b> · ${esc(locked.source_id || "")}<br>
        RA ${Number(locked.ra).toFixed(6)}° · Dec ${Number(locked.dec).toFixed(6)}° · S/N ${Number(locked.snr || 0).toFixed(1)}<br>`;
      if (state.lockedIdentifying) detail += '<span class="layer-tip">正在查询星表…</span>';
      else if (ident) detail += `<span class="catalog-badge ${esc(ident.status)}">${esc(catalogText(ident))}</span> <span class="layer-tip">${esc(catalogReasonText(ident))}</span>`;
      detail += "</div>";
    }
    $("#layerDetail").innerHTML = detail;
  }
}

function renderEmptyAnalysis(d) {
  state.sources = [];
  state.main = [];
  state.diff = [];
  state.stack = null;
  state.cascadeData = null;
  state.tracksData = null;
  const mb = $("#modeBadge");
  mb.className = "badge " + (d.data.mode || "synthetic");
  mb.textContent = { real: "REAL 真实", hybrid: "HYBRID 混合", synthetic: "SIM 模拟", sample: "SAMPLE" }[d.data.mode] || d.data.mode;
  const rb = $("#readyBadge");
  rb.className = "badge l0";
  rb.textContent = "L0 就绪度";
  updateTimeBadge(d.time_filter);
  note(`<b>TIME WINDOW</b> · ${timeFilterText(d.time_filter)}<br>${esc(d.data.note || "时间窗口内没有历元。")}`, "warn");
  const cv = $("#sky");
  const cx = cv.getContext("2d");
  cx.clearRect(0, 0, cv.width, cv.height);
  cx.fillStyle = "#03050a";
  cx.fillRect(0, 0, cv.width, cv.height);
  const aPanel = $("#aSourcePanel");
  if (aPanel) aPanel.classList.add("hidden");
  $("#canvasEmpty").classList.remove("hidden");
  $("#canvasEmpty").innerHTML = `<div class="glyph">◌</div><p>时间窗口内没有历元</p><span>未做测量/拟合，不伪造轨迹；请扩大 t0–t1。</span>`;
  $("#epochLabel").textContent = "窗口内无历元";
  $("#timeLabel").textContent = "—";
  $("#strip").innerHTML = "";
  $("#slider").max = 0;
  $("#slider").value = 0;
  $("#verdict").className = "verdict empty";
  $("#verdict").innerHTML = `<div class="eyebrow">MOTION FINGERPRINT · L0</div><div class="verdict-main"><div class="class-dot">U</div><div><h3>窗口内没有历元</h3><p>没有足够的图像，不能判断运动或变光。</p></div></div>`;
  $("#publicText").classList.add("hidden");
  $("#catalogCard").classList.add("hidden");
  $("#metrics").classList.add("hidden");
  $("#charts").classList.add("hidden");
  $("#evidenceWrap").classList.add("hidden");
  $("#download").disabled = true;
  $("#copyLink").disabled = true;
  if (state.layerMode !== "browse") renderLayerError("时间窗口内没有历元；没有可叠加的分析层。");
  syncURL();
}

function layerScreenPoint(src, n, S) {
  const cx = Number(src.common_x), cy = Number(src.common_y);
  if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
  return [((cx + 0.5) / n) * S, ((cy + 0.5) / n) * S];
}

function drawCascadeOverlay(ctx, n, S) {
  state.layerHits = [];
  const row = currentCascadeEpoch();
  if (!row) return;
  const draw = (list, kind) => {
    for (const src of list || []) {
      const p = layerScreenPoint(src, n, S);
      if (!p) continue;
      const [x, y] = p;
      if (kind === "l1") {
        ctx.beginPath(); ctx.arc(x, y, 3.4, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(95,214,232,.95)"; ctx.fill();
      } else if (kind === "masked") {
        ctx.strokeStyle = "rgba(150,162,188,.8)"; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4);
        ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.arc(x, y, 4.6, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(242,179,94,.95)"; ctx.lineWidth = 1.5; ctx.stroke();
      }
      state.layerHits.push({ x, y, src, kind, row });
    }
  };
  if (state.layerVis.l1) draw(row.l1, "l1");
  if (state.layerVis.masked) draw(row.masked, "masked");
  if (state.layerVis.residual) draw(row.residual, "residual");
  ctx.save();
  ctx.fillStyle = "rgba(5,8,14,.72)";
  ctx.fillRect(10, 10, 245, 25);
  ctx.fillStyle = "#dbe3f2";
  ctx.font = "600 12px 'SF Mono', Menlo, monospace";
  ctx.fillText(`L1 ${row.counts.l1} · masked ${row.counts.masked} · residual ${row.counts.residual}`, 18, 27);
  ctx.restore();
}

const TRACK_COLORS = { D0: "#ef8f9c", D1: "#6ee7a8", D2: "#8f9bb3", D3: "#f2b35e", D4: "#5fd6e8" };

function drawTracksOverlay(ctx, n, S) {
  state.layerHits = [];
  state.trackSegments = [];
  const data = state.tracksData;
  if (!data) return;
  const currentId = state.analysis.epochs[currentFrameIndex()] && state.analysis.epochs[currentFrameIndex()].id;
  const shown = (data.tracks || []).slice(0, 200);
  shown.forEach((track) => {
    const selected = state.selectedTrack && state.selectedTrack.track_id === track.track_id;
    const color = TRACK_COLORS[track.level] || "#8f9bb3";
    const pts = (track.nodes || []).map((node) => ({ node, p: layerScreenPoint(node, n, S) })).filter((x) => x.p);
    if (pts.length < 1) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.globalAlpha = selected ? 1 : 0.42;
    ctx.lineWidth = selected ? 3 : 1.2;
    ctx.setLineDash(track.level === "D0" ? [3, 4] : track.level === "D4" ? [1, 3] : []);
    if (pts.length > 1) {
      ctx.beginPath();
      pts.forEach((x, i) => (i ? ctx.lineTo(x.p[0], x.p[1]) : ctx.moveTo(x.p[0], x.p[1])));
      ctx.stroke();
      for (let i = 1; i < pts.length; i++) {
        state.trackSegments.push({ x1: pts[i - 1].p[0], y1: pts[i - 1].p[1], x2: pts[i].p[0], y2: pts[i].p[1], track });
      }
    }
    ctx.setLineDash([]);
    pts.forEach(({ node, p }) => {
      const here = node.epoch_id === currentId;
      ctx.beginPath();
      ctx.arc(p[0], p[1], here ? 4.8 : 2.4, 0, Math.PI * 2);
      ctx.fillStyle = here ? color : "rgba(5,8,14,.8)";
      ctx.strokeStyle = color;
      ctx.lineWidth = here ? 2 : 1;
      if (here) ctx.fill(); else ctx.stroke();
      state.layerHits.push({ x: p[0], y: p[1], src: node, kind: "track", track });
    });
    if (selected && pts.length) {
      const first = pts[0].p;
      ctx.globalAlpha = 1;
      ctx.fillStyle = color;
      ctx.font = "600 11px 'SF Mono', Menlo, monospace";
      ctx.fillText(track.track_id, first[0] + 8, first[1] - 7);
    }
    ctx.restore();
  });
}

function nearestLayerHit(sx, sy, radius = 12) {
  let best = null, bestD = radius;
  for (const hit of state.layerHits) {
    const d = Math.hypot(hit.x - sx, hit.y - sy);
    if (d <= bestD) { best = hit; bestD = d; }
  }
  return best;
}

function nearestTrackSegment(sx, sy, radius = 7) {
  let best = null, bestD = radius;
  for (const s of state.trackSegments) {
    const dx = s.x2 - s.x1, dy = s.y2 - s.y1;
    const len2 = dx * dx + dy * dy;
    let t = len2 ? ((sx - s.x1) * dx + (sy - s.y1) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    const px = s.x1 + t * dx, py = s.y1 + t * dy;
    const d = Math.hypot(sx - px, sy - py);
    if (d <= bestD) { best = s; bestD = d; }
  }
  return best;
}

function layerHover(e) {
  const [sx, sy] = canvasPoint(e);
  const hit = nearestLayerHit(sx, sy, 14) || (state.layerMode === "tracks" ? null : null);
  $("#sky").style.cursor = hit ? "pointer" : "default";
  if (!hit) { hideTip(); return; }
  const tip = $("#skyTip");
  const src = hit.src;
  const lines = [];
  if (hit.kind === "track") {
    lines.push(`<b>${esc(hit.track.level)} · ${esc(hit.track.model)}</b> · ${esc(hit.track.track_id)}`);
    lines.push(`epoch ${esc(src.epoch_id)} · ${esc(src.stage)} · S/N ${Number(src.snr || 0).toFixed(1)}`);
    lines.push('<span class="tip-ok">点击节点跳帧并调用 /api/identify</span>');
  } else {
    lines.push(`<b>${esc(hit.kind.toUpperCase())}</b> · ${esc(src.stage)} · S/N ${Number(src.snr || 0).toFixed(1)}`);
    lines.push(`RA ${Number(src.ra).toFixed(6)}° · Dec ${Number(src.dec).toFixed(6)}°`);
    lines.push('<span class="tip-ok">点击锁定并调用 /api/identify</span>');
  }
  tip.innerHTML = layerTipHTML(lines);
  tip.classList.remove("hidden");
  const wrap = $(".canvas-wrap").getBoundingClientRect();
  const margin = 8;
  const left = Math.min(Math.max(margin, e.clientX - wrap.left + 14), Math.max(margin, wrap.width - tip.offsetWidth - margin));
  const top = Math.min(Math.max(margin, e.clientY - wrap.top + 14), Math.max(margin, wrap.height - tip.offsetHeight - margin));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function layerTipHTML(lines) {
  return lines.join("<br>");
}

function layerClick(e) {
  const [sx, sy] = canvasPoint(e);
  const hit = nearestLayerHit(sx, sy, 13);
  if (hit) {
    if (hit.kind === "track") {
      selectTrackNode(hit.track, hit.src);
    } else {
      lockLayerSource(hit.src, hit.kind);
    }
    return;
  }
  if (state.layerMode === "tracks") {
    const seg = nearestTrackSegment(sx, sy, 8);
    if (seg) selectTrack(seg.track);
  }
}

function selectTrack(track) {
  state.selectedTrack = track;
  const nodes = track.nodes || [];
  if (nodes.length) {
    const idx = state.analysis.epochs.findIndex((e) => e.id === nodes[0].epoch_id);
    state.frame = idx >= 0 ? idx : Math.min(state.frame, state.analysis.epochs.length - 1);
  }
  renderFrame();
  renderLayerPanel();
}

function selectTrackNode(track, node) {
  state.selectedTrack = track;
  const idx = state.analysis.epochs.findIndex((e) => e.id === node.epoch_id);
  if (idx >= 0) state.frame = idx;
  lockLayerSource(node, "track", track);
  renderFrame();
  renderLayerPanel();
}

function lockLayerSource(node, kind, track) {
  if (!node || node.ra == null || node.dec == null) return;
  const idx = state.analysis.epochs.findIndex((e) => e.id === node.epoch_id);
  const frame = idx >= 0 ? idx : Math.min(state.frame, state.analysis.epochs.length - 1);
  state.locked = {
    ra: Number(node.ra),
    dec: Number(node.dec),
    mjd: Number(node.mjd),
    x: Number(node.common_x != null ? node.common_x : node.x),
    y: Number(node.common_y != null ? node.common_y : node.y),
    frame,
    epoch_id: node.epoch_id,
    snr: Number(node.snr) || 0,
    flux: Number(node.flux) || 0,
    flux_err: Number(node.flux_err) || 0,
    quality: Array.isArray(node.quality) ? node.quality : [],
    saturated: !!node.saturated,
    source_id: node.source_id || "",
    sigma_arcsec: 0.5,
    cascade_source: kind !== "track",
    track_source: kind === "track",
    layer: kind,
    track_id: track ? track.track_id : null,
  };
  state.lockedIdentification = null;
  state.lockedIdentificationKey = null;
  state.lockRequest = null;
  renderLockInfo();
  hideTip();
  sceneNoteForLayer(node, kind, track);
  identifyLockedSource(state.locked);
  renderFrame();
  renderLayerPanel();
}

function sceneNoteForLayer(node, kind, track) {
  const s = state.locked;
  const label = kind === "track" ? `轨迹节点 ${track ? track.track_id : ""}` : `${kind} 源`;
  note(`已锁定 <b>${esc(label)}</b>：RA ${Number(s.ra).toFixed(6)}° · Dec ${Number(s.dec).toFixed(6)}° · S/N ${Number(s.snr).toFixed(1)}；正在查询星表…`);
}


/* ------------------------------------------------------------------ 统一候选目录 */

function fmtNum(v, digits = 4) {
  return v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits);
}

function candidateDirectoryCountsText(c) {
  return `rows ${c.rows} · unique_sources ${c.unique_sources} · tracks ${c.tracks} · known ${c.known} · ambiguous ${c.ambiguous} · no_match ${c.no_match} · insufficient ${c.insufficient} · unknown ${c.unknown} · conflict ${c.conflict || 0}`;
}

async function openCandidateDirectory() {
  const d = state.analysis;
  const panel = $("#candidateDirectoryPanel");
  const note = $("#candidateDirectoryNote");
  if (!d) return;
  if (panel) panel.classList.remove("hidden");
  if (!state.tracksData) {
    try {
      const q = layerQueryString();
      if (!q) throw new Error("没有可用的分析上下文");
      if (note) note.textContent = "正在读取轨迹 tracklet…";
      const r = await fetch(`/api/tracks?${q}`);
      const data = await r.json();
      if (!r.ok || data.error) {
        const e = data.error;
        throw new Error((e && (e.message || e.code)) || "轨迹接口返回错误");
      }
      state.tracksData = data;
    } catch (e) {
      if (note) note.textContent = `无法读取轨迹：${e.message}。未展开候选目录。`;
      return;
    }
  }
  const dir = window.CandidateDirectory.build({
    aSources: state.sources || [], aById: (state.aSourceBatch && state.aSourceBatch.byId) || {},
    cSources: state.anyfieldSources || [], cById: (state.anyfieldBatch && state.anyfieldBatch.byId) || {},
    tracks: (state.tracksData && state.tracksData.tracks) || [],
    epochs: (d && d.epochs) || [],
  });
  try {
    state.candidateDirectory = dir;
    renderCandidateDirectory(dir);
    if (panel) panel.open = true;
  } catch (e) {
    if (note) note.textContent = `候选目录渲染失败：${e.message}。未改变源表/badge。`;
  }
}

function renderCandidateDirectory(dir) {
  const c = dir.counts;
  $("#candidateDirectoryCount").textContent = String(c.rows);
  $("#candidateDirectoryCounts").textContent = candidateDirectoryCountsText(c);
  $("#candidateDirectoryNote").textContent = "候选目录不是发现目录；track 行不是新源，track 节点不另算源。";
  const rows = (dir.rows || []).slice(0, 80).map((row) => {
    const origin = String(row.origin || "").toLowerCase();
    const status = String(row.status || "");
    const epoch = row.origin === "track"
      ? `${esc(row.source_id)} · ${esc(row.level)}`
      : `F${Number(row.frame) + 1} · ${esc(String(row.epoch_id || "").slice(-10))}`;
    const coord = `${fmtNum(row.ra, 4)} / ${fmtNum(row.dec, 4)}`;
    const cat = row.origin === "track" ? (row.catalog || "none") : (row.catalog || "—");
    const catTitle = row.catalog_id ? ` title="${esc(row.catalog_id)}"` : "";
    return `<tr>
      <td class="origin-${esc(origin)}">${esc(row.origin)}</td>
      <td title="${esc(row.epoch_id || row.source_id || "")}">${epoch}</td>
      <td>${esc(coord)}</td>
      <td class="st-${esc(status)}">${esc(status)}</td>
      <td${catTitle}>${esc(cat)}</td>
      <td>${row.separation_arcsec == null ? "—" : fmtNum(row.separation_arcsec, 2)}</td>
      <td title="${esc(row.caveats || "")}">${esc(row.caveats || "—")}</td>
    </tr>`;
  }).join("");
  $("#candidateDirectoryRows").innerHTML = rows || '<tr><td colspan="7">没有候选行</td></tr>';
  ["#exportDirectoryCsvBtn", "#exportDirectoryJsonBtn"].forEach((sel) => {
    const b = $(sel);
    if (b) b.classList.remove("hidden");
  });
}

function exportCandidateDirectory(format) {
  const dir = state.candidateDirectory;
  const note = $("#candidateDirectoryNote");
  try {
    if (!dir || !window.CandidateDirectory) throw new Error("尚未生成统一候选目录");
    const be = window.CandidateDirectory;
    const text = format === "json" ? be.toJson(dir) : be.toCsv(dir.rows);
    const blob = new Blob([text], { type: `${format === "json" ? "application/json" : "text/csv"};charset=utf-8` });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = be.downloadName(format);
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    if (note) note.textContent = `已导出候选目录 ${format.toUpperCase()}：${dir.counts.rows} 行。`;
  } catch (e) {
    if (note) note.textContent = `导出失败：${e.message}。候选表未改变。`;
  }
}

/* ------------------------------------------------------------------ A 源表批量识别 */

function aSourceBadge(result) {
  const ab = window.ASourceBatch;
  if (!ab || !result) return "";
  const st = ab.badgeStatus(result);
  if (!st) return "";
  const label = ab.badgeLabel(st);
  const title = st === "no_match" ? "未收录候选，不是未知星体" : label;
  return `<span class="a-source-badge ${esc(st)}" title="${esc(title)}">${esc(label)}</span>`;
}

function aSourceQuality(src) {
  return (src.quality || []).join(" · ") || (src.saturated ? "SATURATED" : "—");
}

function renderASources(d) {
  const panel = $("#aSourcePanel");
  if (!panel) return;
  const sources = state.sources || [];
  if (!sources.length) {
    panel.classList.add("hidden");
    state.aSourceBatch = { byId: {}, counts: null, error: null };
    state.candidateDirectory = null;
    return;
  }
  panel.classList.remove("hidden");
  $("#aSourceCount").textContent = String(sources.length);
  const batch = state.aSourceBatch || { byId: {}, counts: null };
  const shown = sources.slice(0, 50);
  const rows = shown.map((src) => {
    const key = window.ASourceBatch ? window.ASourceBatch.sourceKey(src) : `${src.epoch_id}:${src.x}:${src.y}`;
    const result = batch.byId && batch.byId[key];
    const q = aSourceQuality(src);
    const qDot = (src.quality || []).length ? '<span class="a-quality-dot" title="' + esc(q) + '">•</span>' : "";
    return `<tr data-a-source-index="${sources.indexOf(src)}" title="${esc(q)}">
      <td class="a-epoch"><b>F${Number(src.frame) + 1}</b> ${qDot}<span>${esc(String(src.epoch_id || "").slice(-10))}</span></td>
      <td>${Number(src.snr || 0).toFixed(1)}</td>
      <td class="a-coord"><span>${Number(src.ra).toFixed(4)}</span><span>${Number(src.dec).toFixed(4)}</span></td>
      <td>${aSourceBadge(result)}</td>
    </tr>`;
  }).join("");
  $("#aSourceRows").innerHTML = rows;
  $("#aSourceRows").onclick = (e) => {
    const tr = e.target.closest("tr[data-a-source-index]");
    if (!tr) return;
    const src = state.sources[Number(tr.dataset.aSourceIndex)];
    if (src) selectSource(src);
  };
  const c = batch.counts;
  $("#aSourceSummary").textContent = c
    ? `known ${c.known_star || 0} / ambiguous ${c.ambiguous || 0} / no_match ${c.no_match || 0} / insufficient ${c.insufficient_data || 0} / unavailable ${c.catalog_unavailable || 0}`
    : (sources.length > 50 ? `显示前 50 / 共 ${sources.length} 个；批量识别计数为全量。` : "共 " + sources.length + " 个；可批量识别星表身份。");
  ["#exportASourcesBtn", "#exportAJsonBtn"].forEach((sel) => {
    const b = $(sel);
    if (b) { b.classList.toggle("hidden", !batch.counts); b.disabled = false; }
  });
  const dirBtn = $("#candidateDirectoryBtn");
  if (dirBtn) dirBtn.classList.remove("hidden");
  $("#aSourceBatchNote").classList.add("hidden");
  if (batch.error) {
    $("#aSourceBatchNote").classList.remove("hidden");
    $("#aSourceBatchNote").textContent = batch.error;
  }
}

async function batchIdentifyASources() {
  const sources = state.sources || [];
  const d = state.analysis;
  if (!sources.length || !d || !window.ASourceBatch) return;
  const btn = $("#batchASourcesBtn");
  const note = $("#aSourceBatchNote");
  const items = window.ASourceBatch.buildBatchSources(sources, d.epochs || []);
  const batches = window.ASourceBatch.chunks(items, 200);
  const old = state.aSourceBatch;
  if (btn) { btn.disabled = true; btn.textContent = "批量识别中…"; }
  ["#exportASourcesBtn", "#exportAJsonBtn"].forEach((sel) => { const b = $(sel); if (b) b.disabled = true; });
  if (note) { note.classList.remove("hidden"); note.textContent = `正在批量识别 A 源 ${sources.length} 个（${batches.length} 批）…`; }
  const merged = {};
  let counts = {};
  try {
    for (let i = 0; i < batches.length; i++) {
      if (note) note.textContent = `正在批量识别 A 源 · 第 ${i + 1}/${batches.length} 批…`;
      const r = await fetch("/api/identify/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sources: batches[i], catalogs: ["gaia", "2mass", "wise"], radius_arcsec: 3.0 }),
      });
      const body = await r.json();
      if (!r.ok || body.error) {
        const e = body.error;
        throw new Error((e && (e.message || e.code)) || "批量识别接口返回错误");
      }
      (body.results || []).forEach((res) => { merged[String(res.source_id || res.detection_id || "")] = res; });
      counts = window.ASourceBatch.mergeCounts(counts, body.counts);
    }
    state.aSourceBatch = { byId: merged, counts, error: null };
    renderASources(d);
    if (note) {
      note.classList.remove("hidden");
      note.textContent = `批量识别完成（gaia+2mass+wise）：${$("#aSourceSummary").textContent}`;
    }
  } catch (e) {
    // 失败不覆盖旧表/旧 badge；只写错误。
    state.aSourceBatch = old;
    if (note) {
      note.classList.remove("hidden");
      note.textContent = `批量识别失败：${e.message}。A 源表和已有 badge 保持不变。`;
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "批量识别 A 源"; }
    ["#exportASourcesBtn", "#exportAJsonBtn"].forEach((sel) => { const b = $(sel); if (b) b.disabled = false; });
  }
}

function downloadTextFile(name, text, mime) {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function batchExportPayload(kind) {
  if (kind === "a") {
    const sources = window.ASourceBatch.buildBatchSources(state.sources || [], (state.analysis && state.analysis.epochs) || []);
    return { sources, catalogs: ["gaia", "2mass", "wise"], radius_arcsec: 3.0 };
  }
  const sources = (state.anyfieldSources || []).filter((s) => Number.isFinite(Number(s.ra)) && Number.isFinite(Number(s.dec)) && Number.isFinite(Number(s.mjd))).map((s) => ({
    source_id: String(s.source_id || ""),
    epoch_id: s.epoch_id != null ? String(s.epoch_id) : "",
    frame: Number.isInteger(Number(s.frame)) ? Number(s.frame) : "",
    x: s.x,
    y: s.y,
    ra: Number(s.ra),
    dec: Number(s.dec),
    mjd: Number(s.mjd),
    sigma_arcsec: 0.5,
  }));
  return { sources, catalogs: ["gaia", "2mass", "wise"], radius_arcsec: 3.0 };
}

async function exportBatchServer(kind, format) {
  const isA = kind === "a";
  const note = $(isA ? "#aSourceBatchNote" : "#anyfieldBatchNote");
  const batch = isA ? state.aSourceBatch : state.anyfieldBatch;
  const n = isA ? (state.sources || []).length : (state.anyfieldSources || []).length;
  if (!batch.counts || !n) {
    if (note) { note.classList.remove("hidden"); note.textContent = `尚未批量识别 ${isA ? "A" : "C"} 源`; }
    return;
  }
  try {
    const payload = batchExportPayload(kind);
    const q = new URLSearchParams({ format, kind: isA ? "A" : "C", target: state.analysis?.target?.id || "target" });
    const r = await fetch(`/api/identify/batch/export?${q.toString()}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!r.ok) {
      let msg = "导出失败";
      try { const e = await r.json(); msg = e.error?.message || e.error?.code || msg; } catch (err) { /* ignore */ }
      throw new Error(msg);
    }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="([^"]+)"/);
    const name = m ? m[1] : (window.BatchExport ? window.BatchExport.downloadName(isA ? "A" : "C", state.analysis?.target?.id || "target", format) : `movescope_${kind}.${format}`);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; a.style.display = "none";
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    if (note) {
      note.classList.remove("hidden");
      note.textContent = `已导出 ${isA ? "A" : "C"} 源 ${n} 个源（CSV 按候选展开；JSON 保留 candidates/byId）。`;
    }
  } catch (e) {
    if (note) {
      note.classList.remove("hidden");
      note.textContent = `导出失败：${e.message}。表格和 badge 未改变。`;
    }
  }
}

function exportABatchCsv() {
  const note = $("#aSourceBatchNote");
  try {
    const be = window.BatchExport;
    if (!be || !state.aSourceBatch.counts || !(state.sources || []).length) {
      throw new Error("尚未批量识别 A 源");
    }
    const rows = be.buildRows(state.sources, state.aSourceBatch.byId, "a", state.analysis && state.analysis.epochs);
    downloadTextFile(be.downloadName("A", state.analysis?.target?.id || "target", "csv"), be.toCsv(rows), "text/csv");
    if (note) {
      note.classList.remove("hidden");
      note.textContent = `已导出 A 源 ${rows.length} 行 CSV（含表头）；导出不改变表格/badge。`;
    }
  } catch (e) {
    if (note) { note.classList.remove("hidden"); note.textContent = `导出失败：${e.message}。表格和 badge 未改变。`; }
  }
}

function exportCBatchCsv() {
  const note = $("#anyfieldBatchNote");
  try {
    const be = window.BatchExport;
    if (!be || !state.anyfieldBatch.counts || !(state.anyfieldSources || []).length) {
      throw new Error("尚未批量识别 C 源");
    }
    const rows = be.buildRows(state.anyfieldSources, state.anyfieldBatch.byId, "c", state.analysis && state.analysis.epochs);
    downloadTextFile(be.downloadName("C", state.analysis?.target?.id || "target", "csv"), be.toCsv(rows), "text/csv");
    if (note) {
      note.classList.remove("hidden");
      note.textContent = `已导出 C 源 ${rows.length} 行 CSV（含表头）；导出不改变表格/badge。`;
    }
  } catch (e) {
    if (note) { note.classList.remove("hidden"); note.textContent = `导出失败：${e.message}。表格和 badge 未改变。`; }
  }
}

/* ------------------------------------------------------------------ 画布 */

function renderFrame() {
  const d = state.analysis;
  if (!d || !d.epochs || !d.epochs.length) return;
  const canvas = $("#sky");
  const ctx = canvas.getContext("2d");
  const S = canvas.width;
  ctx.clearRect(0, 0, S, S);
  ctx.fillStyle = "#03050a";
  ctx.fillRect(0, 0, S, S);

  const i = Math.min(state.frame, d.epochs.length - 1);
  let img = null;
  if (state.mode === "stack" || state.mode === "track") img = state.stack;
  else if (state.mode === "diff") img = state.diff[i];
  else img = state.main[i];
  if (img) {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, S, S);
  }

  const n = d.common_wcs.naxis || 117;
  const toPx = (p) => [((p[0] + 0.5) / n) * S, ((p[1] + 0.5) / n) * S];
  const pts = d.result.target_xy_common || [];

  // 分析层叠加：同一画布，不换页
  if (state.layerMode === "cascade") {
    drawCascadeOverlay(ctx, n, S);
  } else if (state.layerMode === "tracks") {
    drawTracksOverlay(ctx, n, S);
  } else {
    // 普通模式：检测源标记（阶段 A：可点选/已锁定/灰显）
    drawSources(ctx, i, n, S, toPx);
  }

  // 轨迹
  if (pts.length > 1 && (state.mode === "track" || state.mode === "stack")) {
    ctx.save();
    ctx.strokeStyle = "rgba(95,214,232,.75)";
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    pts.forEach((p, k) => {
      const [x, y] = toPx(p);
      k === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    // 拟合弦（首末）
    const [x0, y0] = toPx(pts[0]);
    const [x1, y1] = toPx(pts[pts.length - 1]);
    ctx.strokeStyle = "rgba(242,179,94,.7)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.restore();
  }

  // 各期位置
  if (state.mode === "track") {
    pts.forEach((p, k) => {
      const [x, y] = toPx(p);
      ctx.beginPath();
      ctx.arc(x, y, k === i ? 5 : 3, 0, Math.PI * 2);
      ctx.fillStyle = k === i ? "rgba(242,179,94,.95)" : "rgba(95,214,232,.7)";
      ctx.fill();
    });
  }

  // 当前目标标记（已锁定源与目标重合时不重复画）
  const lockedHere = !!(pts[i] && state.locked && Math.hypot(pts[i][0] - state.locked.x, pts[i][1] - state.locked.y) <= 1.5);
  if (pts[i] && !lockedHere) {
    const [x, y] = toPx(pts[i]);
    ctx.save();
    ctx.strokeStyle = "rgba(255,220,160,.95)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 16, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = "rgba(255,220,160,.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x - 26, y); ctx.lineTo(x - 18, y);
    ctx.moveTo(x + 18, y); ctx.lineTo(x + 26, y);
    ctx.moveTo(x, y - 26); ctx.lineTo(x, y - 18);
    ctx.moveTo(x, y + 18); ctx.lineTo(x, y + 26);
    ctx.stroke();
    ctx.fillStyle = "rgba(255,235,200,.95)";
    ctx.font = "600 17px 'SF Mono', Menlo, monospace";
    ctx.fillText("目标", x + 22, y - 18);
    ctx.restore();
  }

  // 轨迹放大窗：亚像素运动在 6.15″/px 的原图上几乎看不见
  if (state.mode === "track" && pts.length > 1 && img) {
    const xs = pts.map((p) => toPx(p)[0]);
    const ys = pts.map((p) => toPx(p)[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const half = Math.max(14, Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 0.5 + 9);
    const size = 300, pad = 22;
    const ix = S - size - pad, iy = pad;
    const k = img.width / S;
    ctx.save();
    ctx.beginPath();
    ctx.rect(ix, iy, size, size);
    ctx.clip();
    ctx.fillStyle = "#03050a";
    ctx.fillRect(ix, iy, size, size);
    const sx = (cx - half) * k, sy = (cy - half) * k, sw = 2 * half * k, sh = 2 * half * k;
    ctx.drawImage(img, sx, sy, sw, sh, ix, iy, size, size);
    const z = size / (2 * half);
    const Z = (p) => [ix + (toPx(p)[0] - (cx - half)) * z, iy + (toPx(p)[1] - (cy - half)) * z];
    // 轨迹与拟合弦
    ctx.strokeStyle = "rgba(95,214,232,.8)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((p, kk) => { const [x, y] = Z(p); kk ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
    const [ax, ay] = Z(pts[0]);
    const [bx, by] = Z(pts[pts.length - 1]);
    ctx.strokeStyle = "rgba(242,179,94,.85)";
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]);
    pts.forEach((p, kk) => {
      const [x, y] = Z(p);
      ctx.beginPath();
      ctx.arc(x, y, kk === i ? 5.5 : 3.6, 0, Math.PI * 2);
      ctx.fillStyle = kk === i ? "rgba(242,179,94,1)" : "rgba(95,214,232,.9)";
      ctx.fill();
    });
    ctx.restore();
    // 边框与标注
    ctx.strokeStyle = "rgba(150,170,210,.35)";
    ctx.lineWidth = 1;
    ctx.strokeRect(ix + 0.5, iy + 0.5, size - 1, size - 1);
    ctx.fillStyle = "rgba(8,12,20,.82)";
    ctx.fillRect(ix, iy, size, 24);
    ctx.fillStyle = "#9fb0ca";
    ctx.font = "600 12px 'SF Mono', Menlo, monospace";
    ctx.fillText(`轨迹放大 · ${(size / (2 * half)).toFixed(1)}×`, ix + 10, iy + 16);
    ctx.fillStyle = "#f2b35e";
    ctx.textAlign = "right";
    ctx.fillText(`${d.result.fingerprint.displacement_arcsec.toFixed(2)}″`, ix + size - 10, iy + 16);
    ctx.textAlign = "left";
  }

  // 更新文字/滑条/缩略图
  const ep = d.epochs[i];
  $("#epochLabel").textContent = `${(ep.date_obs || "").slice(0, 10) || "日期缺失"} · ${ep.band || "?"} · ${ep.obsid || "—"}`;
  $("#timeLabel").textContent = `MJD ${Number(ep.mjd).toFixed(2)} · ${i + 1}/${d.epochs.length}`;
  $("#slider").value = i;
  $$("#strip button").forEach((b, k) => b.classList.toggle("active", k === i));
}

/* ------------------------------------------------------------------ 源点选（阶段 A） */

const SOURCE_PICK_RADIUS_PX = 12; // 点击最近源阈值（屏幕像素）
const SOURCE_HOVER_RADIUS_PX = 14; // hover 提示稍宽
const LOCK_MATCH_SEP_DEG = 0.00014; // ≈0.5″：跨帧按 RA/Dec 认出同一个源

function currentFrameIndex() {
  const d = state.analysis;
  if (!d || !d.epochs || !d.epochs.length) return 0;
  return Math.min(state.frame, d.epochs.length - 1);
}

function canvasNaxis() {
  return (state.analysis && state.analysis.common_wcs && state.analysis.common_wcs.naxis) || 117;
}

function canvasPoint(e) {
  const c = $("#sky");
  const r = c.getBoundingClientRect();
  return [
    (e.clientX - r.left) * (c.width / Math.max(r.width, 1)),
    (e.clientY - r.top) * (c.height / Math.max(r.height, 1)),
  ];
}

function isLockedSource(s) {
  if (!state.locked) return false;
  const same = s.epoch_id === state.locked.epoch_id && Math.abs(s.x - state.locked.x) < 1e-6 && Math.abs(s.y - state.locked.y) < 1e-6;
  if (same) return true;
  return SourcePick.angularSepDeg(state.locked.ra, state.locked.dec, s.ra, s.dec) <= LOCK_MATCH_SEP_DEG;
}

function drawSources(ctx, frame, n, S, toPx) {
  const list = state.sources || [];
  if (!list.length) return;
  if (state.hover && state.hover.frame !== frame) {
    state.hover = null;
    state.hoverKey = null;
    hideTip();
  }
  const hovered = state.hover && state.hover.frame === frame ? state.hover.source : null;

  const drawLockRing = (x, y) => {
    ctx.save();
    ctx.strokeStyle = "rgba(242,179,94,.95)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = "rgba(242,179,94,.6)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x - 15, y); ctx.lineTo(x - 11, y);
    ctx.moveTo(x + 11, y); ctx.lineTo(x + 15, y);
    ctx.moveTo(x, y - 15); ctx.lineTo(x, y - 11);
    ctx.moveTo(x, y + 11); ctx.lineTo(x, y + 15);
    ctx.stroke();
    const right = x <= S - 76;
    ctx.fillStyle = "rgba(255,217,160,.98)";
    ctx.font = "600 12px 'SF Mono', Menlo, monospace";
    ctx.textAlign = right ? "left" : "right";
    ctx.fillText("已锁定", x + (right ? 13 : -13), y - 11);
    ctx.textAlign = "left";
    ctx.restore();
  };

  const drawHoverRing = (x, y) => {
    ctx.save();
    ctx.strokeStyle = "rgba(232,237,247,.9)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(x, y, 6.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  };

  for (const s of list) {
    if (Number(s.frame) !== frame) continue;
    const [x, y] = toPx([s.x, s.y]);
    if (SourcePick.isSelectable(s)) {
      const r = 2.2 + Math.min(1.8, Math.log10(Math.max(1, Number(s.snr) || 1)) * 0.8);
      ctx.beginPath();
      ctx.arc(x, y, r + 1.3, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(3,5,10,.55)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(95,214,232,.92)";
      ctx.fill();
    } else {
      // 边缘/饱和/低 S/N/帧外：灰显，不可点选
      ctx.beginPath();
      ctx.arc(x, y, 2.0, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(150,162,188,.42)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    if (hovered && hovered === s) drawHoverRing(x, y);
    if (isLockedSource(s)) drawLockRing(x, y);
  }
}

function onCanvasMove(e) {
  if (!state.analysis) return;
  if (state.layerMode !== "browse") {
    layerHover(e);
    return;
  }
  const i = currentFrameIndex();
  const [sx, sy] = canvasPoint(e);
  const S = $("#sky").width;
  const hit = SourcePick.sourceAt(state.sources, i, sx, sy, canvasNaxis(), S, SOURCE_HOVER_RADIUS_PX, {
    includeFlagged: true,
  });
  $("#sky").style.cursor = hit && SourcePick.isSelectable(hit.source) ? "pointer" : "default";
  if (!hit) {
    if (state.hoverKey !== null) {
      state.hover = null;
      state.hoverKey = null;
      hideTip();
      renderFrame();
    } else {
      hideTip();
    }
    return;
  }
  const s = hit.source;
  const key = `${s.epoch_id}:${s.x}:${s.y}`;
  if (key !== state.hoverKey) {
    state.hover = { source: s, frame: i };
    state.hoverKey = key;
    renderFrame();
  }
  showTip(hit, e.clientX, e.clientY);
}

function onCanvasLeave() {
  $("#sky").style.cursor = "default";
  if (state.hover || state.hoverKey) {
    state.hover = null;
    state.hoverKey = null;
    hideTip();
    if (state.analysis) renderFrame();
  } else {
    hideTip();
  }
}

function onCanvasClick(e) {
  if (!state.analysis || state.loading) return;
  if (state.layerMode !== "browse") {
    layerClick(e);
    return;
  }
  const [sx, sy] = canvasPoint(e);
  const S = $("#sky").width;
  const hit = SourcePick.sourceAt(state.sources, currentFrameIndex(), sx, sy, canvasNaxis(), S, SOURCE_PICK_RADIUS_PX);
  if (!hit) return;
  selectSource(hit.source);
}

function showTip(hit, clientX, clientY) {
  const tip = $("#skyTip");
  const s = hit.source;
  const wrap = $(".canvas-wrap").getBoundingClientRect();
  const lines = [
    `<b>S/N ${Number(s.snr).toFixed(1)}</b> · 第 ${Number(s.frame) + 1} 期 · ${s.epoch_id || ""}`,
    `x ${Number(s.x).toFixed(2)} · y ${Number(s.y).toFixed(2)} <span style="color:#66718a">(common px)</span>`,
    `RA ${Number(s.ra).toFixed(6)}° · Dec ${Number(s.dec).toFixed(6)}°`,
  ];
  if (SourcePick.isSelectable(s)) lines.push('<span class="tip-ok">点击锁定并由该坐标重新分析</span>');
  else lines.push(`<span class="tip-warn">${SourcePick.qualityText(s)} · 不可点选</span>`);
  tip.innerHTML = lines.join("<br>");
  tip.classList.remove("hidden");
  const margin = 8;
  const left = Math.min(Math.max(margin, clientX - wrap.left + 14), Math.max(margin, wrap.width - tip.offsetWidth - margin));
  const top = Math.min(Math.max(margin, clientY - wrap.top + 14), Math.max(margin, wrap.height - tip.offsetHeight - margin));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function hideTip() {
  const tip = $("#skyTip");
  if (tip) tip.classList.add("hidden");
}

function selectSource(src) {
  if (!src || !SourcePick.isSelectable(src)) return;
  state.locked = src;
  state.lockedIdentification = null;
  state.lockedIdentificationKey = null;
  state.lockRequest = null;
  state.selected = "custom";
  $("#ra").value = Number(src.ra).toFixed(6);
  $("#dec").value = Number(src.dec).toFixed(6);
  renderPresets();
  renderLockInfo();
  hideTip();
  // 点击即更新 URL（ra/dec/fov + lock=1），分析完成后再按响应归一化。
  const u = new URL(location.href);
  u.searchParams.set("target", "custom");
  u.searchParams.set("ra", Number(src.ra).toFixed(6));
  u.searchParams.set("dec", Number(src.dec).toFixed(6));
  u.searchParams.set("fov", $("#fov").value);
  u.searchParams.set("lock", "1");
  history.replaceState(null, "", u.toString());
  note(
    `已锁定源：RA ${Number(src.ra).toFixed(6)}° · Dec ${Number(src.dec).toFixed(6)}° · S/N ${Number(src.snr).toFixed(1)}；正在以该坐标重新分析…`
  );
  renderFrame();
  identifyLockedSource(src);
  analyze();
}

function lockKey(src) {
  if (!src) return null;
  const ra = Number(src.ra);
  const dec = Number(src.dec);
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
  return `${ra.toFixed(8)}|${dec.toFixed(8)}`;
}

/** A 锁定源后单独调用 /api/identify；失败必须显示“星表不可用”，不能当成未匹配。 */
function updateLockedSourceNote(src, ident) {
  if (!src) return;
  // 级联/轨迹节点：note 直接显示星表识别结果，绝不停留在“正在查询”。
  if (src.cascade_source || src.track_source) {
    const status = ident && ident.status ? ident.status : "catalog_unavailable";
    const label = (ident && ident.label_zh) || CATALOG_TEXT[status] || "星表不可用";
    const layer = src.track_source ? "轨迹节点" : (src.layer || "分析层节点");
    note(`已锁定 <b>${esc(layer)}</b> · RA ${Number(src.ra).toFixed(6)}° · Dec ${Number(src.dec).toFixed(6)}° · ${esc(label)}（${esc(status)}）`);
    return;
  }
  // A 源锁定后的 note 由 analyze 负责（“正在以该坐标重新分析…”），这里只更新 C 源，
  // 避免 identify 晚到把 analyze 的状态覆盖掉。
  if (!src || !src.c_source) return;
  if (window.LockNote && typeof window.LockNote.sourceNote === "function") {
    note(window.LockNote.sourceNote(src, ident || { status: "catalog_unavailable", label_zh: "星表不可用" }));
    return;
  }
  const ra = Number(src.ra);
  const dec = Number(src.dec);
  const status = ident && ident.status ? ident.status : "catalog_unavailable";
  const label = (ident && ident.label_zh) || CATALOG_TEXT[status] || "星表不可用";
  note(`已锁定 C 源 RA <b>${Number.isFinite(ra) ? ra.toFixed(6) : "—"}°</b> · Dec <b>${Number.isFinite(dec) ? dec.toFixed(6) : "—"}°</b> · ${esc(label)}（${esc(status)}）`);
}

async function identifyLockedSource(src) {
  const d = state.analysis;
  if (!d || !src) return;
  const key = lockKey(src);
  const ep = (d.epochs || [])[Number(src.frame)] || (d.epochs || [])[0] || {};
  const mjd = Number.isFinite(Number(src.mjd)) ? Number(src.mjd) : Number(ep.mjd);
  const scale = Number(d.common_wcs && d.common_wcs.scale_arcsec) || 6.15;
  const sx = Number(src.sigma_x);
  const sy = Number(src.sigma_y);
  const sigmaPx = Math.max(0.03, Math.hypot(Number.isFinite(sx) ? sx : 0.5, Number.isFinite(sy) ? sy : 0.5)) + 0.05;
  const givenSigma = Number(src.sigma_arcsec);
  const sigma = Number.isFinite(givenSigma) && givenSigma > 0 ? givenSigma : Math.max(0.3, sigmaPx * scale);
  if (!Number.isFinite(mjd) || !Number.isFinite(Number(src.ra)) || !Number.isFinite(Number(src.dec))) {
    state.lockedIdentification = {
      status: "catalog_unavailable",
      label_zh: "星表不可用",
      caveats: ["缺少坐标/MJD，无法查询星表。"],
      discovery_claim: false,
    };
    renderLockInfo();
    updateLockedSourceNote(src, state.lockedIdentification);
    return;
  }
  state.lockedIdentifying = true;
  state.lockedIdentificationKey = key;
  renderLockInfo();
  const payload = {
    ra: Number(src.ra),
    dec: Number(src.dec),
    mjd,
    sigma_arcsec: sigma,
    detection_id: src.c_source
      ? `c:${String(src.source_id)}`
      : `${src.epoch_id || "epoch"}:${Number(src.x).toFixed(2)}:${Number(src.y).toFixed(2)}`,
  };
  try {
    const r = await fetch("/api/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const ident = await r.json();
    if (!r.ok || ident.error) {
      const e = ident.error;
      const msg = typeof e === "string" ? e : (e && (e.message || e.code)) || "识别接口返回错误";
      throw new Error(msg);
    }
    if (state.lockedIdentificationKey !== key) return;
    state.lockedIdentification = ident;
  } catch (e) {
    if (state.lockedIdentificationKey !== key) return;
    state.lockedIdentification = {
      status: "catalog_unavailable",
      label_zh: "星表不可用",
      catalog_release: null,
      source_id: null,
      sep_arcsec: null,
      confidence: 0,
      caveats: ["星表识别请求失败：" + e.message],
      discovery_claim: false,
      disclaimer: "未匹配不等于新天体；星表不可用不代表“未收录”。",
    };
  } finally {
    if (state.lockedIdentificationKey === key) {
      state.lockedIdentifying = false;
      renderLockInfo();
      updateLockedSourceNote(src, state.lockedIdentification);
      if (state.layerMode !== "browse") renderLayerPanel();
    }
  }
}

function clearLock(showNote = true) {
  const had = !!state.locked;
  state.locked = null;
  state.lockedIdentification = null;
  state.lockedIdentificationKey = null;
  state.lockedIdentifying = false;
  state.lockRequest = null;
  const u = new URL(location.href);
  if (u.searchParams.has("lock")) {
    u.searchParams.delete("lock");
    history.replaceState(null, "", u.toString());
  }
  renderLockInfo();
  if (state.analysis) renderFrame();
  if (state.layerMode !== "browse") renderLayerPanel();
  if (showNote && had) note("已解除源锁定；坐标输入保留，可继续编辑或重新分析。");
}

function resolveLock(d) {
  if (state.locked && state.locked.c_source) {
    // C 源锁定独立于 A 分析目标；不要因为 analyze 的目标坐标不同而清掉它。
  } else if (state.locked) {
    // 分析坐标已变（例如用户换了目标）→ 旧锁定不再适用于当前结果
    const sep = SourcePick.angularSepDeg(state.locked.ra, state.locked.dec, d.target.ra, d.target.dec);
    if (!Number.isFinite(sep) || sep > 1e-5) {
      state.locked = null;
    } else {
      // 新一次分析有自己的源对象：用最新测量刷新 x/y/snr（高亮按 RA/Dec 匹配）
      const near = SourcePick.nearestSourceByRaDec(state.sources, state.locked.ra, state.locked.dec, 0.0015);
      if (near) {
        const changed = lockKey(state.locked) !== lockKey(near.source);
        state.locked = near.source;
        if (changed) {
          state.lockedIdentification = null;
          state.lockedIdentificationKey = null;
        }
      }
    }
  }
  if (!state.locked && state.lockRequest) {
    // URL 带 lock=1：分析完成后按坐标找回源，恢复高亮（可复现）
    const hit = SourcePick.nearestSourceByRaDec(state.sources, state.lockRequest.ra, state.lockRequest.dec, 0.0015);
    if (hit) {
      state.locked = hit.source;
      if (!state.lockedIdentification) identifyLockedSource(hit.source);
    }
    state.lockRequest = null;
  }
  renderLockInfo();
}

function lockedIdentification() {
  const s = state.locked;
  if (!s) return null;
  const key = lockKey(s);
  if (state.lockedIdentification && state.lockedIdentificationKey === key) {
    return state.lockedIdentification;
  }
  // 兼容旧路径：锁定源就是分析目标时，/api/analyze 的 identification 仍可用。
  const d = state.analysis;
  if (!d || !d.identification || !d.target) return null;
  const sep = SourcePick.angularSepDeg(d.target.ra, d.target.dec, s.ra, s.dec);
  return Number.isFinite(sep) && sep <= 1e-5 ? d.identification : null;
}

function renderLockInfo() {
  const el = $("#lockInfo");
  const s = state.locked;
  if (!s) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const ident = lockedIdentification();
  const sourceId = ident && (ident.source_id || ident.catalog_id);
  const release = ident && (ident.catalog_release || ident.release);
  const caveats = ident && Array.isArray(ident.caveats) ? ident.caveats.slice(0, 2) : [];
  const catLine = state.lockedIdentifying
    ? `<div class="lock-catalog"><span class="catalog-badge">识别中</span><em>正在查询 Gaia DR3 星表…</em></div>`
    : ident
      ? `<div class="lock-catalog"><span class="catalog-badge ${esc(ident.status)}">${esc(catalogText(ident))}</span>
          ${sourceId ? `<code>${esc(sourceId)}</code>` : ""}
          ${release ? `<span class="catalog-chip">${esc(release)}</span>` : ""}
          <em>${esc(catalogReasonText(ident))}</em>
          ${caveats.map((c) => `<div class="catalog-note">${esc(c)}</div>`).join("")}
        </div>`
      : "";
  const origin = s.c_source
    ? `C ingested · ${String(s.source_id || "")}`
    : s.track_source
      ? `轨迹 ${s.track_id || ""} · 第 ${Number(s.frame) + 1} 期`
      : s.cascade_source
        ? `${String(s.layer || "节点")} · 第 ${Number(s.frame) + 1} 期`
        : `第 ${Number(s.frame) + 1} 期`;
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="lock-head"><span class="lock-dot"></span>已锁定源<button id="unlockBtn" class="ghost small" type="button">解除</button></div>
    <div class="lock-line">RA <b>${Number(s.ra).toFixed(6)}°</b> · Dec <b>${Number(s.dec).toFixed(6)}°</b></div>
    <div class="lock-line">S/N <b>${Number(s.snr).toFixed(1)}</b> · flux ${Number(s.flux).toFixed(2)} ± ${Number(s.flux_err).toFixed(2)} · ${esc(origin)} · ${SourcePick.qualityText(s)}</div>
    ${catLine}
    <div class="lock-hint">${s.cascade_source || s.track_source ? "仅查询星表身份；不触发重新分析，残差/轨迹候选不是新天体。" : "坐标已填入上方并用于分析；C 源由 /api/identify 单独比对星表。"}</div>`;
}

function syncURL() {
  const d = state.analysis;
  if (!d || !d.target) return;
  const u = new URL(location.href);
  const isPreset = d.target.id && d.target.id !== "custom";
  u.searchParams.set("target", isPreset ? d.target.id : "custom");
  u.searchParams.set("ra", Number(d.target.ra).toFixed(6));
  u.searchParams.set("dec", Number(d.target.dec).toFixed(6));
  const fov = analysisFov();
  if (Number.isFinite(fov)) u.searchParams.set("fov", String(fov));
  u.searchParams.set("mode", state.layerMode);
  const win = analysisTimeParams();
  if (win.ok && win.t0 != null) {
    u.searchParams.set("t0", String(win.t0));
    u.searchParams.set("t1", String(win.t1));
  } else {
    u.searchParams.delete("t0");
    u.searchParams.delete("t1");
  }
  if (state.fromView) u.searchParams.set("from", "view");
  else u.searchParams.delete("from");
  if (state.locked) u.searchParams.set("lock", "1");
  else u.searchParams.delete("lock");
  history.replaceState(null, "", u.toString());
  updateTimeBadge(d.time_filter);
  setLayerModeUI(state.layerMode);
}

/* ------------------------------------------------------------------ 播放 */

function togglePlay() {
  if (!state.analysis) return;
  state.playing ? stopPlay() : startPlay();
}

function startPlay() {
  if (!state.analysis) return;
  state.playing = true;
  $("#play").textContent = "❚❚";
  state.timer = setInterval(() => {
    state.frame = (state.frame + 1) % state.analysis.epochs.length;
    renderFrame();
  }, 700);
}

function stopPlay() {
  state.playing = false;
  $("#play").textContent = "▶";
  clearInterval(state.timer);
}

/* ------------------------------------------------------------------ 图表 */

function chartCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 340;
  const h = w * (canvas.id === "lc" ? 0.39 : 0.53);
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  canvas.style.height = h + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function drawCharts(d) {
  const t = d.result && d.result.track ? d.result.track.t : [];
  if (!t || t.length < 1) {
    $("#charts").classList.add("hidden");
    return;
  }
  $("#charts").classList.remove("hidden");
  drawLightCurve(d);
  drawTrackPlot(d);
}

function drawLightCurve(d) {
  const { ctx, w, h } = chartCanvas($("#lc"));
  ctx.fillStyle = "#060a12";
  ctx.fillRect(0, 0, w, h);
  if (d.result.multi_band) {
    ctx.fillStyle = "#66718a";
    ctx.font = "12px 'SF Mono', Menlo, monospace";
    ctx.fillText("多波段历元：不同探测器流量不可直接比较", 16, h / 2 - 4);
    ctx.fillStyle = "#47536b";
    ctx.font = "11px 'SF Mono', Menlo, monospace";
    ctx.fillText("未做绝对流量定标，因此不评估变光", 16, h / 2 + 16);
    $("#lcMeta").textContent = "多波段 · 不评估变光";
    return;
  }
  const tr = d.result.track;
  const flux = tr.flux;
  const med = median(flux.filter((x) => Number.isFinite(x)));
  const pad = { l: 34, r: 8, t: 12, b: 20 };
  const t0 = Math.min(...tr.t), t1 = Math.max(...tr.t);
  const ys = flux.map((f) => f / (med || 1));
  const yMin = Math.min(...ys, 0.98), yMax = Math.max(...ys, 1.02);
  const X = (t) => pad.l + ((t - t0) / Math.max(t1 - t0, 1e-6)) * (w - pad.l - pad.r);
  const Y = (v) => h - pad.b - ((v - yMin) / Math.max(yMax - yMin, 1e-6)) * (h - pad.t - pad.b);

  ctx.strokeStyle = "rgba(255,255,255,.05)";
  ctx.lineWidth = 1;
  for (let k = 0; k <= 3; k++) {
    const y = pad.t + ((h - pad.t - pad.b) * k) / 3;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
  }
  ctx.fillStyle = "#66718a";
  ctx.font = "10px 'SF Mono', Menlo, monospace";
  ctx.fillText(yMax.toFixed(2), 4, pad.t + 9);
  ctx.fillText(yMin.toFixed(2), 4, h - pad.b);
  ctx.fillText("相对流量", pad.l, h - 5);

  if (d.result.fingerprint.amp_mag > 0.08) {
    const amp = Math.pow(10, 0.4 * d.result.fingerprint.amp_mag / 2);
    ctx.fillStyle = "rgba(239,143,156,.07)";
    ctx.fillRect(pad.l, Y(amp), w - pad.l - pad.r, Y(1 / amp) - Y(amp));
  }
  ctx.strokeStyle = "rgba(242,179,94,.55)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  tr.t.forEach((t, i) => {
    const x = X(t), y = Y(ys[i]);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
  tr.t.forEach((t, i) => {
    ctx.beginPath();
    ctx.arc(X(t), Y(ys[i]), 3.2, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,215,150,.95)";
    ctx.fill();
  });
  $("#lcMeta").textContent = `${tr.t.length} 期 · 基准 ${med.toFixed(2)}`;
}

function drawTrackPlot(d) {
  const { ctx, w, h } = chartCanvas($("#track"));
  const tr = d.result.track;
  const pad = { l: 40, r: 14, t: 14, b: 26 };
  ctx.fillStyle = "#050810";
  ctx.fillRect(0, 0, w, h);
  const n = tr.xi.length;
  if (!n) return;
  const xs = tr.xi, ys = tr.eta;
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1.2), spanY = Math.max(maxY - minY, 1.2);
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  const halfX = spanX * 0.72, halfY = spanY * 0.72;
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  // 等比例：同一角秒在 x/y 上占相同像素
  const scale = Math.min(plotW / (2 * halfX), plotH / (2 * halfY));
  const X = (v) => pad.l + plotW / 2 + (v - cx) * scale;
  const Y = (v) => pad.t + plotH / 2 - (v - cy) * scale;

  ctx.strokeStyle = "rgba(255,255,255,.06)";
  for (let k = -2; k <= 2; k++) {
    const xv = cx + (k / 2) * halfX, yv = cy + (k / 2) * halfY;
    ctx.beginPath(); ctx.moveTo(X(xv), pad.t); ctx.lineTo(X(xv), h - pad.b); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad.l, Y(yv)); ctx.lineTo(w - pad.r, Y(yv)); ctx.stroke();
  }
  // 首末弦
  ctx.strokeStyle = "rgba(242,179,94,.55)";
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(X(xs[0]), Y(ys[0])); ctx.lineTo(X(xs[n - 1]), Y(ys[n - 1])); ctx.stroke();
  ctx.setLineDash([]);
  // 误差十字 + 点（颜色随时间由青到琥珀）
  for (let i = 0; i < n; i++) {
    const x = X(xs[i]), y = Y(ys[i]);
    const ex = Math.min(14, Math.max(3, tr.sx[i] * scale));
    const ey = Math.min(14, Math.max(3, tr.sy[i] * scale));
    ctx.strokeStyle = "rgba(95,214,232,.55)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x - ex, y); ctx.lineTo(x + ex, y); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x, y - ey); ctx.lineTo(x, y + ey); ctx.stroke();
    const t = i / Math.max(n - 1, 1);
    ctx.beginPath(); ctx.arc(x, y, i === 0 || i === n - 1 ? 4.6 : 3.5, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${Math.round(95 + 147 * t)},${Math.round(214 - 35 * t)},${Math.round(232 - 170 * t)},.95)`;
    ctx.fill();
  }
  ctx.fillStyle = "#66718a";
  ctx.font = "10px 'SF Mono', Menlo, monospace";
  ctx.fillText("东 ξ (″)", w - 66, h - 8);
  ctx.save();
  ctx.translate(12, pad.t + 52);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("北 η (″)", 0, 0);
  ctx.restore();
  ctx.fillText(`${(maxX - minX).toFixed(1)}″`, pad.l + 2, pad.t + 10);
  ctx.fillText(`${(maxY - minY).toFixed(1)}″`, pad.l + 2, pad.t + 24);
  $("#trackMeta").textContent = `Δξ ${(maxX - minX).toFixed(2)}″ · Δη ${(maxY - minY).toFixed(2)}″`;
}

function median(a) {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)];
}

/* ------------------------------------------------------------------ 导出 */

function downloadJSON() {
  if (!state.analysis) return;
  const blob = new Blob([JSON.stringify(state.analysis, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `movescope-${state.analysis.target.id}-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function copyLink() {
  const d = state.analysis;
  if (!d) return;
  const u = new URL(location.href);
  const preset = state.targets.find((t) => t.id === d.target.id);
  const same = preset && Math.abs(preset.ra - d.target.ra) < 1e-6 && Math.abs(preset.dec - d.target.dec) < 1e-6;
  if (same) u.searchParams.set("target", d.target.id);
  else u.searchParams.set("target", "custom");
  u.searchParams.set("ra", d.target.ra);
  u.searchParams.set("dec", d.target.dec);
  u.searchParams.set("fov", String(analysisFov() || parseFloat($("#fov").value)));
  u.searchParams.set("mode", state.layerMode);
  const win = analysisTimeParams();
  if (win.ok && win.t0 != null) { u.searchParams.set("t0", String(win.t0)); u.searchParams.set("t1", String(win.t1)); }
  else { u.searchParams.delete("t0"); u.searchParams.delete("t1"); }
  if (state.fromView) u.searchParams.set("from", "view");
  if (state.locked) u.searchParams.set("lock", "1");
  else u.searchParams.delete("lock");
  navigator.clipboard?.writeText(u.toString());
  const btn = $("#copyLink");
  btn.textContent = "已复制";
  setTimeout(() => (btn.textContent = "复制可嵌入链接"), 1400);
}

init();
