// main.js — 状态、UI 绑定、数据管线
import { createGlobe } from './globe.js';
import { createSkyMap } from './skymap.js';
import * as A from './astro.js';

const $ = (id) => document.getElementById(id);

// MoveScope 前端地址（app/server.py 提供，默认 8790）
const MOVESCOPE_ORIGIN = 'http://127.0.0.1:8790';
const PRESET_FOV = { barnard: 0.5, epsind_b: 0.5, wise0855: 0.2, rw_aur: 0.5, r_dra: 0.5, m101: 0.1, campo_a: 0.1 };

// 覆盖口径：单片 3.49° 方视场的半对角线 2.47°（中心落在这个半径里才算“这帧拍到了该天区”）
const HIT_RADIUS_DEG = 2.47;
// 认定“就是该预设目标”的门限：1 角秒
const TARGET_MATCH_ARCSEC = 1.0;

const state = {
  ra: 269.452077,
  dec: 4.693365,
  fov: 3.5,
  mjd: 61316.5267,
  mode: 'data',
  playing: false,
  speed: 0.6,
};

let fields = [];
let targets = [];
let epochIndex = {};
let mjdMin = 0;
let mjdMax = 1;
let globe = null;
let sky = null;
let hitStats = { frames: 0, exposures: 0, nights: 0, groups: [] };
let nExposures = 0;
let nWeeks = 0;

// ---------------------------------------------------------------- 工具
let toastTimer = 0;
function toast(msg, ms = 2400) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

async function copy(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} 已复制`);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    toast(`${label} 已复制`);
  }
}

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

/** 与预设目标的角距（角秒），无匹配返回 Infinity */
function targetSepArcsec(t) {
  return A.angSep(t.ra, t.dec, state.ra, state.dec) * 3600;
}

function matchedTarget() {
  let best = null;
  let bestSep = Infinity;
  for (const t of targets) {
    const s = targetSepArcsec(t);
    if (s < bestSep) { bestSep = s; best = t; }
  }
  return best && bestSep <= TARGET_MATCH_ARCSEC ? best : null;
}

// ---------------------------------------------------------------- 天区
function cleanRa(v) {
  // 只在越界时归一化，避免把 269.452077 变成 269.4520769999999
  return v >= 0 && v < 360 ? v : A.norm360(v);
}

function setRegion({ ra, dec, fov }, { focus = false, recenter = true, rezoom = false } = {}) {
  if (ra !== undefined) state.ra = cleanRa(ra);
  if (dec !== undefined) state.dec = clamp(dec, -89.5, 89.5);
  if (fov !== undefined) state.fov = clamp(fov, 0.05, 180);

  sky?.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov }, { recenter, rezoom });
  globe?.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov });
  if (focus) globe?.focus(state.ra, state.dec);

  syncRegionUI();
  scheduleUrl();
}

function syncRegionUI() {
  $('in-ra').value = state.ra.toFixed(2);
  $('in-dec').value = state.dec.toFixed(2);
  $('in-fov').value = state.fov.toFixed(2);
  $('ra-hms').textContent = A.fmtRa(state.ra);
  $('dec-dms').textContent = A.fmtDec(state.dec);
  $('ro-ra').textContent = A.fmtRa(state.ra);
  $('ro-dec').textContent = A.fmtDec(state.dec);
  $('ro-fov').textContent = `${state.fov.toFixed(2)}° × ${state.fov.toFixed(2)}°`;

  const g = A.equatorialToGalactic(state.ra, state.dec);
  $('ro-gal').textContent = `l ${g.l.toFixed(1)}°  b ${g.b.toFixed(1)}°`;

  updateSite();
  updateHits();
  updatePresetState();
  updateMoveScopeHint();
}

function updateSite() {
  const site = A.zenithOf(state.ra, state.dec, state.mjd);
  const lst = A.lst(site.lon, state.mjd);
  $('ro-site').textContent = `${A.fmtDec(site.lat)}  ${site.lon >= 0 ? 'E' : 'W'}${Math.abs(site.lon).toFixed(1)}°`;
  $('ro-lst').textContent = A.fmtRa(lst);
  return site;
}

// ---------------------------------------------------------------- 覆盖命中
function computeHits() {
  const frames = [];
  for (const f of fields) {
    if (Math.abs(f.dec - state.dec) > HIT_RADIUS_DEG + 0.2) continue;
    const sep = A.angSep(f.ra, f.dec, state.ra, state.dec);
    if (sep <= HIT_RADIUS_DEG) frames.push({ f, sep });
  }
  const byObs = new Map();
  for (const h of frames) {
    const cur = byObs.get(h.f.obsid);
    if (!cur) {
      byObs.set(h.f.obsid, {
        obsid: h.f.obsid, ra: h.f.ra, dec: h.f.dec, mjd: h.f.mjd, n: 1, sep: h.sep,
      });
    } else {
      cur.n += 1;
      if (h.sep < cur.sep) cur.sep = h.sep;
      if (h.f.mjd < cur.mjd) cur.mjd = h.f.mjd;
    }
  }
  const groups = [...byObs.values()].sort((a, b) => a.sep - b.sep);
  const nights = new Set(frames.map((h) => A.fmtUtc(h.f.mjd).slice(0, 10)));
  return { frames: frames.length, exposures: byObs.size, nights: nights.size, groups };
}

function updateHits() {
  hitStats = computeHits();
  const { frames, exposures, nights } = hitStats;
  $('hits-count').textContent = `${frames} 帧 · ${exposures} 曝光 · ${nights} 夜`;

  const box = $('hits');
  box.innerHTML = '';

  const target = matchedTarget();
  const curated = target ? epochIndex[target.id] || [] : [];
  const cEl = $('curated');
  if (curated.length) {
    cEl.hidden = false;
    const near = curated.reduce((b, e) => (Math.abs(e.mjd - state.mjd) < Math.abs(b.mjd - state.mjd) ? e : b), curated[0]);
    cEl.innerHTML = `<b>策展历元 ${curated.length}</b> · 数据库 epoch_catalog · 跳到 ${A.fmtUtc(near.mjd).slice(0, 10)}`;
    cEl.onclick = () => {
      setRegion({ ra: target.ra, dec: target.dec }, { focus: true, recenter: true });
      setClock(near.mjd, 'data');
    };
  } else {
    cEl.hidden = true;
    cEl.onclick = null;
  }

  if (!hitStats.groups.length) {
    const d = document.createElement('div');
    d.className = 'empty';
    d.innerHTML = `本地索引在 ${HIT_RADIUS_DEG}° 内无命中。<br>
      这不等于“没被拍过”：本地只是 ${nWeeks} 个观测周的子集（${fields.length.toLocaleString('en-US')} 帧 /
      ${nExposures.toLocaleString('en-US')} 次曝光），换一条覆盖带或点“现在”看别的时相。`;
    box.appendChild(d);
    return;
  }

  for (const g of hitStats.groups.slice(0, 40)) {
    const row = document.createElement('div');
    row.className = 'hit';
    row.innerHTML = `<span class="id">${g.obsid} <i>· ${g.n} 帧</i></span><span class="sep"><b>${(g.sep * 60).toFixed(1)}′</b></span>`;
    row.title = `${A.fmtUtc(g.mjd)} · ${g.n} 帧`;
    row.addEventListener('click', () => {
      setRegion({ ra: g.ra, dec: g.dec }, { focus: true, recenter: true });
      setClock(g.mjd, 'data');
      toast(`定位到 ${g.obsid}`);
    });
    box.appendChild(row);
  }
}

function updatePresetState() {
  const t = matchedTarget();
  document.querySelectorAll('.preset[data-id]').forEach((el) => {
    el.classList.toggle('on', !!t && el.dataset.id === t.id);
  });
}

// ---------------------------------------------------------------- MoveScope 交接
/** 传给 MoveScope 的 URL：命中目标时带 target + 预设全精度坐标；否则只带自定义坐标。 */
function moveScopeUrl() {
  const t = matchedTarget();
  const p = new URLSearchParams();
  if (t) {
    p.set('target', t.id);
    p.set('ra', String(t.ra));
    p.set('dec', String(t.dec));
  } else {
    p.set('ra', String(state.ra));
    p.set('dec', String(state.dec));
  }
  p.set('fov', String(state.fov));
  return `${MOVESCOPE_ORIGIN}/?${p.toString()}`;
}

function updateMoveScopeHint() {
  const t = matchedTarget();
  const el = $('ms-hint');
  if (!el) return;
  if (t) {
    el.innerHTML = `将打开 <b>${t.name}</b>（REAL 预设 · ${t.id}）`;
    el.classList.remove('warn');
  } else {
    el.innerHTML = '自定义天区 → MoveScope 会走 <b>SIM</b> 路径（数据桥接未完成前如此）';
    el.classList.add('warn');
  }
}

/** 用 GET 探活；no-cors 下只要服务器应答就 resolve，连不上则 reject。 */
async function moveScopeAlive() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 1500);
  try {
    await fetch(`${MOVESCOPE_ORIGIN}/api/health`, { mode: 'no-cors', cache: 'no-store', signal: ctrl.signal });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------- 时间
function setClock(mjd, mode = 'data', silent = false) {
  state.mjd = clamp(mjd, mjdMin, mjdMax);
  state.mode = mode;
  globe?.setClock(state.mjd);
  $('clock-date').textContent = `${A.fmtUtc(state.mjd)} UTC`;
  $('clock-mjd').textContent = `MJD ${state.mjd.toFixed(4)}`;
  $('clock-mode').textContent = mode === 'now' ? '实时' : '数据时相';
  $('time').value = String(((state.mjd - mjdMin) / (mjdMax - mjdMin)) * 1000);
  const site = updateSite();
  updateLabels(site);
  const near = fields.some((f) => Math.abs(f.mjd - state.mjd) < 0.5);
  $('tracknote').textContent = near ? '轨迹窗口 ±12 h' : '该时相 ±12 h 无曝光';
  if (!silent) scheduleUrl();
}

function goNow() {
  const now = A.dateToMjd(new Date());
  state.playing = false;
  setPlaying(false);
  state.mjd = now;
  globe?.setClock(now);
  state.mode = 'now';
  $('clock-date').textContent = `${A.fmtUtc(now)} UTC`;
  $('clock-mjd').textContent = `MJD ${now.toFixed(4)}`;
  $('clock-mode').textContent = '实时';
  $('time').value = String(clamp(((now - mjdMin) / (mjdMax - mjdMin)) * 1000, 0, 1000));
  const site = updateSite();
  updateLabels(site);
  $('tracknote').textContent = '该时相 ±12 h 无曝光';
  if (Math.abs(now - mjdMax) > 40) {
    toast(`本地索引止于 ${A.fmtUtc(mjdMax).slice(0, 10)}，实时时刻无地面轨迹`, 3200);
  }
}

function setPlaying(on) {
  state.playing = on;
  $('btn-play').classList.toggle('playing', on);
}

// ---------------------------------------------------------------- 标签投影
function updateLabels(site = updateSite()) {
  if (!globe) return;
  const elSite = $('lb-site');
  const elSky = $('lb-sky');

  const m = globe.selectionScreen();
  elSite.hidden = m.behind;
  elSky.hidden = m.behind;
  if (m.behind) return;

  const visW = $('labels').clientWidth || window.innerWidth;
  const visH = $('labels').clientHeight || window.innerHeight;
  const leftPct = (m.x / visW) * 100;
  const topPct = (m.y / visH) * 100;

  elSky.style.left = `${leftPct.toFixed(2)}%`;
  elSky.style.top = `${topPct.toFixed(2)}%`;
  elSky.style.transform = `translate(-50%, ${-m.halfPx - 26}px)`;
  const t = matchedTarget();
  $('lb-sky-val').textContent = t
    ? `${t.name} · ${state.fov.toFixed(2)}°`
    : `${A.fmtRa(state.ra)} ${A.fmtDec(state.dec)} · ${state.fov.toFixed(2)}°`;

  elSite.style.left = `${leftPct.toFixed(2)}%`;
  elSite.style.top = `${topPct.toFixed(2)}%`;
  elSite.style.transform = `translate(-50%, ${m.halfPx + 12}px)`;
  $('lb-site-val').textContent = `天顶 ${A.fmtDec(site.lat)} ${site.lon >= 0 ? 'E' : 'W'}${Math.abs(site.lon).toFixed(1)}°`;
}

// ---------------------------------------------------------------- URL 状态
let urlTimer = 0;
function scheduleUrl() {
  clearTimeout(urlTimer);
  urlTimer = setTimeout(writeUrl, 300);
}

function writeUrl() {
  const t = matchedTarget();
  const p = new URLSearchParams();
  p.set('ra', String(state.ra));
  p.set('dec', String(state.dec));
  p.set('fov', Number(state.fov.toFixed(2)).toString());
  if (t) p.set('target', t.id);
  p.set('t', state.mjd.toFixed(4));
  const url = `${location.pathname}?${p.toString()}`;
  history.replaceState(null, '', url);
}

/** search 优先，hash 兜底（兼容旧链接）。非法值忽略。 */
function readUrlState() {
  const s = new URLSearchParams(location.search);
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  const get = (k) => (s.has(k) ? s.get(k) : h.has(k) ? h.get(k) : null);
  const num = (k) => {
    const v = get(k);
    if (v == null) return undefined;
    const f = parseFloat(v);
    return Number.isFinite(f) ? f : undefined;
  };
  return { ra: num('ra'), dec: num('dec'), fov: num('fov'), t: num('t'), target: get('target') };
}

// ---------------------------------------------------------------- 时间轴密度
function drawDensity() {
  const cv = $('density');
  const w = cv.clientWidth || 600;
  const h = 26;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const bins = 220;
  const hist = new Float32Array(bins);
  for (const f of fields) {
    const i = Math.min(bins - 1, Math.max(0, Math.floor(((f.mjd - mjdMin) / (mjdMax - mjdMin)) * bins)));
    hist[i] += 1;
  }
  const peak = Math.max(...hist, 1);
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < bins; i++) {
    if (!hist[i]) continue;
    const bh = Math.max(2, (hist[i] / peak) * (h - 4));
    const x = (i / bins) * w;
    const bw = Math.max(1, w / bins - 0.6);
    ctx.fillStyle = `rgba(87,230,255,${0.10 + 0.32 * (hist[i] / peak)})`;
    ctx.fillRect(x, h - 2 - bh, bw, bh);
  }
}

// ---------------------------------------------------------------- 启动
async function boot() {
  const [fr, tr, ei] = await Promise.all([
    fetch('./data/fields.json').then((r) => r.json()),
    fetch('./data/targets.json').then((r) => r.json()),
    fetch('./data/epoch_index.json').then((r) => r.json()).catch(() => ({ targets: {} })),
  ]);

  fields = fr.fields
    .map(([obsid, det, ra, dec, mjd, lat, lon, elon, elat, xposure]) => ({
      obsid, det, ra, dec, mjd, lat, lon, elon, elat, xposure,
    }))
    .sort((a, b) => a.mjd - b.mjd);

  targets = (tr.targets || []).map((t) => ({ ...t, short: t.name }));
  epochIndex = ei.targets || {};
  mjdMin = fields[0].mjd;
  mjdMax = fields[fields.length - 1].mjd;

  const nExp = new Set(fields.map((f) => f.obsid)).size;
  nExposures = nExp;
  nWeeks = new Set(fields.map((f) => f.obsid.split('_').slice(0, 2).join('_'))).size;
  $('chip-count').textContent = `${fields.length.toLocaleString('en-US')} 帧 / ${nExp.toLocaleString('en-US')} 曝光`;
  $('chip-range').textContent = `${A.fmtUtc(mjdMin).slice(0, 10)} → ${A.fmtUtc(mjdMax).slice(0, 10)}`;
  $('t-start').textContent = A.fmtUtc(mjdMin).slice(0, 10);
  $('t-end').textContent = A.fmtUtc(mjdMax).slice(0, 10);
  $('tracknote').textContent = `轨迹窗口 ±12 h · ${A.mjdToDate(mjdMax).getUTCFullYear()}`;
  const noteEl = $('data-note');
  if (noteEl) {
    noteEl.innerHTML = `数据：<span class="mono">data/fields.json</span>（由 <span class="mono">tools/build_field_index.py</span>
      从数据库 <span class="mono">frames</span> 表导出）· ${fields.length.toLocaleString('en-US')} 帧 /
      ${nExp.toLocaleString('en-US')} 次曝光。`;
  }

  const presetRow = $('presets');
  for (const t of targets) {
    const b = document.createElement('button');
    b.className = 'preset';
    b.dataset.id = t.id;
    b.textContent = t.name.replace(/（.*?）/g, '').replace(' 视场', '');
    b.title = `RA ${t.ra}  DEC ${t.dec}`;
    b.addEventListener('click', () => {
      setRegion({ ra: t.ra, dec: t.dec, fov: PRESET_FOV[t.id] ?? 0.5 }, { focus: true, recenter: true, rezoom: true });
      const curated = epochIndex[t.id] || [];
      if (curated.length) {
        const near = curated.reduce((b2, e) => (Math.abs(e.mjd - state.mjd) < Math.abs(b2.mjd - state.mjd) ? e : b2), curated[0]);
        setClock(near.mjd, 'data', true);
      }
    });
    presetRow.appendChild(b);
  }

  // 三维地球（先明确检查 WebGL2，失败时给可读的错误而不是“请启动服务”）
  try {
    const probe = document.createElement('canvas').getContext('webgl2');
    if (!probe) throw new Error('WebGL2 unavailable');
  } catch {
    throw new Error('WebGL2 unavailable');
  }
  globe = createGlobe($('globe'), {
    onHover: (ll) => {
      const el = $('cursor');
      if (!ll) { el.classList.remove('on'); return; }
      const s = A.skyOverhead(ll.lat, ll.lon, state.mjd);
      el.classList.add('on');
      el.innerHTML = `<span>光标</span> RA ${A.fmtRa(s.ra)} <i>·</i> DEC ${A.fmtDec(s.dec)}`;
    },
    onPick: (lat, lon) => {
      const s = A.skyOverhead(lat, lon, state.mjd);
      setRegion({ ra: s.ra, dec: s.dec }, { recenter: true });
      toast(`天顶天区 → RA ${A.fmtRa(s.ra)}  DEC ${A.fmtDec(s.dec)}`);
    },
  });
  globe.setFields(fields);

  // 天图
  sky = createSkyMap({
    canvas: $('skymap'),
    minimap: $('minimap'),
    fields,
    targets,
    onChange: (v) => {
      state.ra = v.ra;
      state.dec = v.dec;
      state.fov = v.fov;
      globe.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov });
      syncRegionUI();
      scheduleUrl();
    },
    onHover: (skyPos) => {
      document.querySelector('.skymap-wrap').title =
        `RA ${A.fmtRa(skyPos.ra)}  DEC ${A.fmtDec(skyPos.dec)}`;
    },
  });

  // 初始状态：search 优先，hash 兜底
  const u = readUrlState();
  const urlTarget = u.target ? targets.find((t) => t.id === u.target) : null;
  if (urlTarget) {
    state.ra = urlTarget.ra;
    state.dec = urlTarget.dec;
    state.fov = PRESET_FOV[urlTarget.id] ?? 0.5;
  }
  if (u.ra !== undefined) state.ra = cleanRa(u.ra);
  if (u.dec !== undefined) state.dec = clamp(u.dec, -89.5, 89.5);
  if (u.fov !== undefined) state.fov = clamp(u.fov, 0.05, 180);
  const t0 = u.t !== undefined ? clamp(u.t, mjdMin, mjdMax) : mjdMax;

  sky.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov }, { recenter: true, rezoom: true });
  globe.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov });
  globe.focus(state.ra, state.dec, true);
  setClock(t0, 'data', true);
  syncRegionUI();
  drawDensity();
  writeUrl();

  // 交互
  let inputTimer = 0;
  function afterInput() {
    clearTimeout(inputTimer);
    inputTimer = setTimeout(() => {
      sky.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov }, { recenter: true });
      globe.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov });
      syncRegionUI();
      scheduleUrl();
    }, 140);
  }
  $('in-ra').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    if (Number.isFinite(v)) { state.ra = cleanRa(v); afterInput(); }
  });
  $('in-dec').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    if (Number.isFinite(v)) { state.dec = clamp(v, -89.5, 89.5); afterInput(); }
  });
  $('in-fov').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    if (Number.isFinite(v)) { state.fov = clamp(v, 0.05, 180); afterInput(); }
  });

  $('btn-now').addEventListener('click', goNow);

  $('btn-play').addEventListener('click', () => {
    if (!state.playing && (state.mode === 'now' || state.mjd > mjdMax - 0.6)) {
      setClock(mjdMin, 'data', true);
    }
    setPlaying(!state.playing);
  });

  $('time').addEventListener('input', (e) => {
    setPlaying(false);
    const frac = parseFloat(e.target.value) / 1000;
    setClock(mjdMin + frac * (mjdMax - mjdMin), 'data');
  });

  document.querySelectorAll('.sbtn').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.sbtn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.speed = parseFloat(b.dataset.speed);
    });
  });

  $('tg-track').addEventListener('change', (e) => globe.setOptions({ showTrack: e.target.checked }));
  $('tg-cov').addEventListener('change', (e) => globe.setOptions({ showCoverage: e.target.checked }));
  $('tg-mapcov').addEventListener('change', (e) => toggleSkyCoverage(e.target.checked));

  $('btn-json').addEventListener('click', () => {
    const site = A.zenithOf(state.ra, state.dec, state.mjd);
    const t = matchedTarget();
    const payload = {
      ra: state.ra,
      dec: state.dec,
      fov: state.fov,
      mjd: state.mjd,
      target_id: t ? t.id : null,
      site: { lat: site.lat, lon: site.lon },
      coverage: { frames: hitStats.frames, exposures: hitStats.exposures, nights: hitStats.nights, radius_deg: HIT_RADIUS_DEG },
    };
    copy(JSON.stringify(payload, null, 2), '天区 JSON');
  });

  $('btn-link').addEventListener('click', () => copy(location.href, '链接'));

  $('btn-movescope').addEventListener('click', async () => {
    const url = moveScopeUrl();
    const win = window.open('about:blank', '_blank');
    const alive = await moveScopeAlive();
    if (alive) {
      if (win) win.location.replace(url);
      else { copy(url, 'MoveScope 链接'); toast('浏览器拦截了新标签页，链接已复制', 3200); }
    } else {
      if (win) win.close();
      copy(url, 'MoveScope 链接');
      toast(`连不上 ${MOVESCOPE_ORIGIN}：先启动 MoveScope（app/server.py），链接已复制`, 4200);
    }
  });

  window.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    if (e.code === 'Space') { e.preventDefault(); $('btn-play').click(); }
    else if (e.key === 'ArrowLeft') { setClock(state.mjd - (e.shiftKey ? 1 : 0.05)); }
    else if (e.key === 'ArrowRight') { setClock(state.mjd + (e.shiftKey ? 1 : 0.05)); }
    else if (e.key === 'n' || e.key === 'N') goNow();
  });

  // 动画循环：播放 + 标签投影
  let last = performance.now();
  function tick(now) {
    requestAnimationFrame(tick);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (state.playing) {
      let m = state.mjd + dt * state.speed;
      if (m > mjdMax) m = mjdMin;
      state.mjd = m;
      state.mode = 'data';
      globe.setClock(m);
      $('time').value = String(((m - mjdMin) / (mjdMax - mjdMin)) * 1000);
      $('clock-date').textContent = `${A.fmtUtc(m)} UTC`;
      $('clock-mjd').textContent = `MJD ${m.toFixed(4)}`;
    }
    updateLabels();
  }
  requestAnimationFrame(tick);

  await globe.ready;
  setTimeout(() => $('loader').classList.add('done'), 350);
  setTimeout(() => $('loader').remove(), 1200);
}

// 天图覆盖开关：靠重设 fields 实现（保持接口简单）
function toggleSkyCoverage(on) {
  if (!sky) return;
  sky.setFields(on ? fields : []);
}

window.addEventListener('resize', () => {
  drawDensity();
});

function fatal(err) {
  console.error(err);
  const msg = String(err?.message || err);
  const isWebgl = /webgl|context/i.test(msg);
  const l = document.getElementById('loader');
  if (!l) return;
  l.innerHTML = isWebgl
    ? `<div style="max-width:440px;text-align:center;font-family:ui-monospace,monospace;font-size:11px;line-height:1.9;color:#ffb454">
        浏览器不支持 <b>WebGL2</b>（或已被禁用），三维地球无法初始化。<br><br>
        <span style="color:#7d90a6">这是显卡/浏览器设置问题，不是网络问题。请在 Chrome / Safari 新版中打开，或检查
        chrome://gpu 的 WebGL2 状态。</span>
      </div>`
    : `<div style="max-width:440px;text-align:center;font-family:ui-monospace,monospace;font-size:11px;line-height:1.9;color:#ffb454">
        装载失败：${msg}<br><br>
        本页面依赖 <span style="color:#d7e8f8">fetch</span> 与 ES module，需通过本地 HTTP 服务打开：<br>
        <span style="color:#d7e8f8">cd 视图工具 &amp;&amp; ./start.sh</span>（默认 8830）<br>
        然后访问 <span style="color:#d7e8f8">http://127.0.0.1:8830/</span>
      </div>`;
}

boot().catch(fatal);
