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
  hover: null, // { source, frame } 当前 hover 的源
  hoverKey: null,
  lockRequest: null, // URL 带 lock=1 时，分析完成后按坐标重新锁定
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
  const payload = {
    target_id: preset && sameAsPreset ? preset.id : "custom",
    ra,
    dec,
    fov,
    mode: $("#mode").value,
  };
  if (!Number.isFinite(payload.ra) || !Number.isFinite(payload.dec)) {
    note("请输入有效的 RA / Dec（度）。", "err");
    return;
  }
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
    state.frame = 0;
    state.hover = null;
    state.hoverKey = null;
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
  state.main = await Promise.all(d.epochs.map((e) => loadImage(e.main_url)));
  state.diff = await Promise.all(d.epochs.map((e) => loadImage(e.diff_url)));
  state.stack = await loadImage(d.stack_url);
}

/* ------------------------------------------------------------------ 渲染 UI */

function renderAll(d, dt) {
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
  note(
    `<b>${(d.data.mode || "").toUpperCase()}</b> · ${nEpoch} 期 · ${(d.data.bands || []).join("/")} · ${fetchTxt}${fitTxt}${baseTxt}${fb} · ${dt}s<br>${d.data.note || ""}`
  );

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
  const v = $("#verdict");
  v.className = "verdict";
  v.dataset.class = r.class;
  v.innerHTML = `
    <div class="eyebrow">MOTION FINGERPRINT · ${r.readiness}</div>
    <div class="verdict-main">
      <div class="class-dot">${cls.letter}</div>
      <div>
        <h3>${isSample ? "样本核验（不做测量）" : (r.label_zh || cls.label)}</h3>
        <p>${isSample ? "两张图来自不同天区 · 仅验证解析与渲染" : cls.sub + (r.moving ? " · 位移显著" : "") + (r.variable ? " · 变光显著" : "")}</p>
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
  renderFrame();
}

function renderMetrics(r) {
  const fp = r.fingerprint;
  const moving = r.moving;
  const mu = moving
    ? `${(fp.mu_mas_yr / 1000).toFixed(2)} <small>″/yr</small>`
    : `&lt; ${((fp.mu_err_mas_yr || 0) * 3 / 1000).toFixed(2)} <small>3σ</small>`;
  const pa = moving && fp.pa_deg != null ? `${fp.pa_deg.toFixed(0)}°` : "—";
  const amp = fp.amp_mag > 0.25 ? `${fp.amp_mag.toFixed(2)} <small>mag</small>` : "—";
  const period = fp.delta_bic_var > 6 && fp.period_d ? `${fp.period_d.toFixed(1)} <small>d</small>` : "—";
  const acc = fp.accel_sig > 2 ? `${fp.accel_sig.toFixed(1)}σ` : "—";
  const lin = moving && Number.isFinite(fp.linearity) ? `${(fp.linearity * 100).toFixed(1)}%` : "—";

  const cards = [
    ["自行 μ", mu, moving],
    ["方向 PA", pa, false],
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

/* ------------------------------------------------------------------ 画布 */

function renderFrame() {
  const d = state.analysis;
  if (!d) return;
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

  // 检测源标记（阶段 A：可点选/已锁定/灰显）
  drawSources(ctx, i, n, S, toPx);

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
  if (!d) return 0;
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
  analyze();
}

function clearLock(showNote = true) {
  const had = !!state.locked;
  state.locked = null;
  state.lockRequest = null;
  const u = new URL(location.href);
  if (u.searchParams.has("lock")) {
    u.searchParams.delete("lock");
    history.replaceState(null, "", u.toString());
  }
  renderLockInfo();
  if (state.analysis) renderFrame();
  if (showNote && had) note("已解除源锁定；坐标输入保留，可继续编辑或重新分析。");
}

function resolveLock(d) {
  if (state.locked) {
    // 分析坐标已变（例如用户换了目标）→ 旧锁定不再适用于当前结果
    const sep = SourcePick.angularSepDeg(state.locked.ra, state.locked.dec, d.target.ra, d.target.dec);
    if (!Number.isFinite(sep) || sep > 1e-5) {
      state.locked = null;
    } else {
      // 新一次分析有自己的源对象：用最新测量刷新 x/y/snr（高亮按 RA/Dec 匹配）
      const near = SourcePick.nearestSourceByRaDec(state.sources, state.locked.ra, state.locked.dec, 0.0015);
      if (near) state.locked = near.source;
    }
  }
  if (!state.locked && state.lockRequest) {
    // URL 带 lock=1：分析完成后按坐标找回源，恢复高亮（可复现）
    const hit = SourcePick.nearestSourceByRaDec(state.sources, state.lockRequest.ra, state.lockRequest.dec, 0.0015);
    if (hit) state.locked = hit.source;
    state.lockRequest = null;
  }
  renderLockInfo();
}

function renderLockInfo() {
  const el = $("#lockInfo");
  const s = state.locked;
  if (!s) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="lock-head"><span class="lock-dot"></span>已锁定源<button id="unlockBtn" class="ghost small" type="button">解除</button></div>
    <div class="lock-line">RA <b>${Number(s.ra).toFixed(6)}°</b> · Dec <b>${Number(s.dec).toFixed(6)}°</b></div>
    <div class="lock-line">S/N <b>${Number(s.snr).toFixed(1)}</b> · flux ${Number(s.flux).toFixed(2)} ± ${Number(s.flux_err).toFixed(2)} · 第 ${Number(s.frame) + 1} 期 · ${SourcePick.qualityText(s)}</div>
    <div class="lock-hint">坐标已填入上方并用于分析；画布上为琥珀色锁定环。</div>`;
}

function syncURL() {
  const d = state.analysis;
  if (!d || !d.target) return;
  const u = new URL(location.href);
  const isPreset = d.target.id && d.target.id !== "custom";
  u.searchParams.set("target", isPreset ? d.target.id : "custom");
  u.searchParams.set("ra", Number(d.target.ra).toFixed(6));
  u.searchParams.set("dec", Number(d.target.dec).toFixed(6));
  const fov = parseFloat($("#fov").value);
  if (Number.isFinite(fov)) u.searchParams.set("fov", String(fov));
  if (state.locked) u.searchParams.set("lock", "1");
  else u.searchParams.delete("lock");
  history.replaceState(null, "", u.toString());
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
  u.searchParams.set("fov", $("#fov").value);
  if (state.locked) u.searchParams.set("lock", "1");
  else u.searchParams.delete("lock");
  navigator.clipboard?.writeText(u.toString());
  const btn = $("#copyLink");
  btn.textContent = "已复制";
  setTimeout(() => (btn.textContent = "复制可嵌入链接"), 1400);
}

init();
