// main.js — 状态、UI 绑定、数据管线
import { createGlobe } from './globe.js';
import { createSkyMap } from './skymap.js';
import * as A from './astro.js';

const $ = (id) => document.getElementById(id);

// MoveScope 前端地址（app/server.py 提供，默认 8790）。
// 可在页面加载前用 window.MOVESCOPE_ORIGIN 覆盖（部署/联调时不必改源码）。
const MOVESCOPE_ORIGIN = String(
  window.MOVESCOPE_ORIGIN || 'http://127.0.0.1:8790'
).replace(/\/+$/, '');
const PRESET_FOV = { barnard: 0.5, epsind_b: 0.5, wise0855: 0.2, rw_aur: 0.5, r_dra: 0.5, m101: 0.1, campo_a: 0.1 };
// 分析层时间窗口：以当前数据时相为中心，过滤 MoveScope 分析层历元范围。
const WINDOW_OPTIONS = [30, 90, 180, 365, 730];
const DEFAULT_WINDOW_DAYS = 365;
// 播放速度 = 每个观测历元的停留时间（不是连续时间倍率）
const STEP_MS = { 0.5: 2400, 1: 1200, 2: 600 };

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
  speed: 1,
  windowDays: DEFAULT_WINDOW_DAYS,
  t0: 0,
  t1: 0,
};

let fields = [];
let targets = [];
let epochIndex = {};
let observationTimes = [];
let playTimer = 0;
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

async function copy(text, label, { silent = false } = {}) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    ta.remove();
  }
  if (!silent) {
    toast(ok ? `${label} 已复制` : `${label} 复制失败，请手动复制`, ok ? 2400 : 3600);
  }
  return ok;
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
  updateAnalysisHint();
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
      commitTime(near.mjd, { animate: true, source: 'curated' });
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
      commitTime(g.mjd, { animate: true, source: 'hit' });
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
/** 一次 handoff：当前天区 + 分析时间窗口交给 MoveScope 主界面。 */
function moveScopeUrl() {
  const p = new URLSearchParams();
  p.set('ra', String(state.ra));
  p.set('dec', String(state.dec));
  p.set('fov', String(state.fov));
  p.set('t0', state.t0.toFixed(4));
  p.set('t1', state.t1.toFixed(4));
  p.set('from', 'view');
  return `${MOVESCOPE_ORIGIN}/?${p.toString()}`;
}

/** 打开 MoveScope；先 GET 探活，失败时复制链接并给出可读提示。 */
async function openMoveScope(url) {
  const win = window.open('about:blank', '_blank');
  const alive = await moveScopeAlive();
  if (alive) {
    if (win) {
      win.location.replace(url);
      return;
    }
    const copied = await copy(url, 'MoveScope 链接', { silent: true });
    toast(copied ? '浏览器拦截了新标签页，MoveScope 链接已复制' : '浏览器拦截了新标签页；请手动打开 MoveScope', 4200);
    return;
  }
  if (win) win.close();
  const copied = await copy(url, 'MoveScope 链接', { silent: true });
  toast(
    copied
      ? `连不上 ${MOVESCOPE_ORIGIN}：先启动 MoveScope（app/server.py），链接已复制`
      : `连不上 ${MOVESCOPE_ORIGIN}：先启动 MoveScope（app/server.py）`,
    5200,
  );
}

function updateMoveScopeHint() {
  const t = matchedTarget();
  const el = $('ms-hint');
  if (!el) return;
  el.classList.remove('warn');
  if (t) {
    el.innerHTML = `将打开 <b>${t.name}</b> 天区；MoveScope 按坐标分析，进入后可切换模式。`;
  } else {
    el.innerHTML = '自定义天区；MoveScope 按坐标分析（有真实历元则 REAL，否则 SIM），进入后可切换模式。';
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

// ---------------------------------------------------------------- 分析时间窗口
function normalizeWindowDays(days) {
  const d = Number(days);
  if (!Number.isFinite(d) || d <= 0) return DEFAULT_WINDOW_DAYS;
  return WINDOW_OPTIONS.find((opt) => Math.abs(opt - d) < 0.01) ?? d;
}

/** 修改半宽后，以当前 state.mjd 为中心重算 t0/t1。 */
function applyWindowDays(days, { silent = false } = {}) {
  const d = Number(days);
  if (!Number.isFinite(d) || d <= 0) return;
  state.windowDays = normalizeWindowDays(d);
  state.t0 = state.mjd - state.windowDays;
  state.t1 = state.mjd + state.windowDays;
  syncWindowUI();
  if (!silent) scheduleUrl();
}

function syncWindowUI() {
  if (!Number.isFinite(state.t0) || !Number.isFinite(state.t1)) {
    state.t0 = state.mjd - state.windowDays;
    state.t1 = state.mjd + state.windowDays;
  }
  const t0 = state.t0;
  const t1 = state.t1;
  const range = $('win-range');
  if (!range) return;
  range.textContent = `MJD ${t0.toFixed(3)} – ${t1.toFixed(3)}`;
  $('win-utc').textContent = `${A.fmtUtc(t0).slice(0, 16)} → ${A.fmtUtc(t1).slice(0, 16)} UTC`;

  const sel = $('win-days');
  if (sel) {
    const value = String(state.windowDays);
    let opt = [...sel.options].find((o) => o.value === value);
    if (!opt) {
      opt = [...sel.options].find((o) => o.dataset.custom === '1');
      if (!opt) {
        opt = document.createElement('option');
        opt.dataset.custom = '1';
        sel.appendChild(opt);
      }
      opt.value = value;
      opt.textContent = `自定义 ${state.windowDays.toFixed(1)} 天`;
    }
    sel.value = value;
  }
  updateAnalysisHint();
}

function updateAnalysisHint() {
  const el = $('analysis-hint');
  if (!el) return;
  const fovWarn = state.fov > 3.0
    ? `<br><span style="color:var(--amber)">当前 FOV ${state.fov.toFixed(2)}° 超出 MoveScope 分析页 3.0° 上限，请先调小 FOV。</span>`
    : '';
  el.innerHTML = `一次 handoff：进入 MoveScope 后切换 <b>普通 / 级联-残差 / 轨迹</b>；t0–t1 是分析层过滤范围，不是数据时相。${fovWarn}`;
}

// ---------------------------------------------------------------- 时间（唯一入口）
const OBS_EPS = 1e-7;

function stepDwellMs() {
  return STEP_MS[state.speed] ?? STEP_MS[1];
}

/** 返回严格晚于 mjd 的第一个观测索引；没有则 -1。 */
function nextObservationIndex(mjd) {
  let lo = 0;
  let hi = observationTimes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (observationTimes[mid] <= mjd + OBS_EPS) lo = mid + 1;
    else hi = mid;
  }
  return lo < observationTimes.length ? lo : -1;
}

/**
 * 所有时间入口的唯一提交点。
 * 同步 state.mjd、时钟/MJD、滑杆、UTC 输入、分析窗口、URL、地球角度。
 */
function commitTime(mjd, { animate = false, source = 'set', silent = false, allowOutside = false } = {}) {
  if (!Number.isFinite(mjd)) return { mjd: state.mjd, clamped: false };
  const bounded = clamp(mjd, mjdMin, mjdMax);
  const next = allowOutside ? mjd : bounded;
  state.mjd = next;
  state.mode = source === 'now' ? 'now' : 'data';
  state.t0 = next - state.windowDays;
  state.t1 = next + state.windowDays;

  globe?.setClock(next, { animate });
  $('clock-date').textContent = `${A.fmtUtc(next)} UTC`;
  $('clock-mjd').textContent = `MJD ${next.toFixed(4)}`;
  $('clock-mode').textContent =
    source === 'play' || source === 'play-start'
      ? '观测序列'
      : state.mode === 'now'
        ? '实时'
        : '数据时相';
  const frac = (next - mjdMin) / (mjdMax - mjdMin);
  $('time').value = String(clamp(frac * 1000, 0, 1000));
  const utcInput = $('in-utc');
  if (utcInput) utcInput.value = A.fmtUtc(next).slice(0, 16);

  const site = updateSite();
  updateLabels(site);
  const near = fields.some((f) => Math.abs(f.mjd - next) < 0.5);
  $('tracknote').textContent = near ? '轨迹窗口 ±12 h' : '该时相 ±12 h 无曝光';
  syncWindowUI();
  if (state.playing && source !== 'play' && source !== 'play-start') scheduleNextPlayback();
  if (!silent) scheduleUrl();
  return { mjd: next, clamped: !allowOutside && Math.abs(next - mjd) > 1e-9 };
}

/** 解析 UTC 输入 YYYY-MM-DD HH:MM；非法返回 null。 */
function parseUtcInput(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/.exec(String(text).trim());
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const h = Number(m[4]);
  const mi = Number(m[5]);
  const dt = new Date(Date.UTC(y, mo - 1, d, h, mi, 0));
  if (
    dt.getUTCFullYear() !== y ||
    dt.getUTCMonth() !== mo - 1 ||
    dt.getUTCDate() !== d ||
    dt.getUTCHours() !== h ||
    dt.getUTCMinutes() !== mi
  ) {
    return null;
  }
  return A.dateToMjd(dt);
}

function locateUtc() {
  const mjd = parseUtcInput($('in-utc').value);
  if (mjd === null) {
    toast('UTC 时间格式应为 YYYY-MM-DD HH:MM（UTC）', 3600);
    return;
  }
  const r = commitTime(mjd, { animate: true, source: 'utc' });
  if (r.clamped) {
    toast(`UTC 时间超出本地索引，已定位到 ${A.fmtUtc(r.mjd).slice(0, 16)} UTC`, 3600);
  }
}

function clearPlayTimer() {
  if (playTimer) {
    clearTimeout(playTimer);
    playTimer = 0;
  }
}

function stopPlayback({ toastMsg = '' } = {}) {
  const was = state.playing;
  state.playing = false;
  clearPlayTimer();
  $('btn-play').classList.toggle('playing', false);
  if (was) {
    syncWindowUI();
    scheduleUrl();
  }
  if (toastMsg) toast(toastMsg, 3200);
}

function scheduleNextPlayback(delay = stepDwellMs()) {
  clearPlayTimer();
  if (!state.playing) return;
  playTimer = setTimeout(playbackStep, Math.max(0, delay));
}

function playbackStep() {
  playTimer = 0;
  if (!state.playing) return;
  const idx = nextObservationIndex(state.mjd);
  if (idx < 0) {
    stopPlayback({ toastMsg: '已到最后一个观测，播放停止' });
    return;
  }
  commitTime(observationTimes[idx], { animate: true, source: 'play' });
  if (idx >= observationTimes.length - 1) {
    stopPlayback({ toastMsg: '已到最后一个观测，播放停止' });
    return;
  }
  scheduleNextPlayback();
}

function startPlayback() {
  if (!observationTimes.length) {
    toast('没有可用观测序列', 3200);
    return;
  }
  const last = observationTimes[observationTimes.length - 1];
  if (state.mjd >= last - OBS_EPS) {
    toast('已到最后一个观测，播放不会循环回开头', 3600);
    return;
  }
  const idx = nextObservationIndex(state.mjd);
  if (idx < 0) {
    toast('已到最后一个观测，播放不会循环回开头', 3600);
    return;
  }
  const prev = idx > 0 ? observationTimes[idx - 1] : null;
  const onObservation = prev !== null && Math.abs(prev - state.mjd) <= OBS_EPS;
  if (!onObservation) {
    // 当前时间不在观测序列：先跳到下一个真实观测。
    commitTime(observationTimes[idx], { animate: true, source: 'play-start' });
    if (idx >= observationTimes.length - 1) {
      toast('已到最后一个观测，播放停止', 3600);
      return;
    }
  }
  state.playing = true;
  $('btn-play').classList.toggle('playing', true);
  scheduleNextPlayback();
}

function togglePlayback() {
  if (state.playing) stopPlayback();
  else startPlayback();
}

function goNow() {
  const now = A.dateToMjd(new Date());
  stopPlayback();
  commitTime(now, { animate: true, source: 'now', allowOutside: true });
  if (Math.abs(now - mjdMax) > 40) {
    toast(`本地索引止于 ${A.fmtUtc(mjdMax).slice(0, 10)}，实时时刻无地面轨迹`, 3200);
  }
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
  p.set('t0', state.t0.toFixed(4));
  p.set('t1', state.t1.toFixed(4));
  const url = `${location.pathname}?${p.toString()}`;
  history.replaceState(null, '', url);
}

/** search 优先，hash 兜底（兼容旧链接）。非法值忽略，回退默认。 */
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
  const dec = num('dec');
  const fov = num('fov');
  const t0 = num('t0');
  const t1 = num('t1');
  return {
    ra: num('ra'),
    dec: dec !== undefined && dec >= -90 && dec <= 90 ? dec : undefined,
    fov: fov !== undefined && fov >= 0.05 && fov <= 180 ? fov : undefined,
    t: num('t'),
    t0: t0 !== undefined && t0 > 0 ? t0 : undefined,
    t1: t1 !== undefined && t1 > 0 ? t1 : undefined,
    target: get('target'),
  };
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
  observationTimes = [...new Set(fields.map((f) => f.mjd))].sort((a, b) => a - b);

  targets = (tr.targets || []).map((t) => ({ ...t, short: t.name }));
  epochIndex = ei.targets || {};
  mjdMin = fields[0].mjd;
  mjdMax = fields[fields.length - 1].mjd;

  const nExp = new Set(fields.map((f) => f.obsid)).size;
  nExposures = nExp;
  nWeeks = new Set(fields.map((f) => f.obsid.split('_').slice(0, 2).join('_'))).size;
  $('chip-count').textContent = `${fields.length.toLocaleString('en-US')} 帧 / ${nExp.toLocaleString('en-US')} 曝光`;
  $('chip-range').textContent = `${A.fmtUtc(mjdMin).slice(0, 10)} → ${A.fmtUtc(mjdMax).slice(0, 10)}`;
  const axisSpan = mjdMax - mjdMin;
  const axisTicks = [
    ['t-start', 0],
    ['t-25', 0.25],
    ['t-mid', 0.5],
    ['t-75', 0.75],
    ['t-end', 1],
  ];
  for (const [id, frac] of axisTicks) {
    $(id).textContent = A.fmtUtc(mjdMin + axisSpan * frac).slice(0, 10);
  }
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
        commitTime(near.mjd, { animate: true, source: 'preset' });
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

  let startMjd = u.t !== undefined ? clamp(u.t, mjdMin, mjdMax) : mjdMax;
  if (u.t0 !== undefined && u.t1 !== undefined && u.t1 > u.t0) {
    const half = normalizeWindowDays((u.t1 - u.t0) / 2);
    if (half > 0) {
      state.windowDays = half;
      if (u.t === undefined) startMjd = clamp((u.t0 + u.t1) / 2, mjdMin, mjdMax);
    }
  }

  sky.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov }, { recenter: true, rezoom: true });
  globe.setSelection({ ra: state.ra, dec: state.dec, fov: state.fov });
  globe.focus(state.ra, state.dec, true);
  commitTime(startMjd, { animate: false, source: 'boot', silent: true });
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

  $('btn-play').addEventListener('click', togglePlayback);

  $('time').addEventListener('input', (e) => {
    const frac = parseFloat(e.target.value) / 1000;
    commitTime(mjdMin + frac * (mjdMax - mjdMin), { animate: true, source: 'slider' });
  });

  $('btn-utc-go').addEventListener('click', locateUtc);
  $('in-utc').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      locateUtc();
    }
  });
  document.querySelectorAll('[data-hours]').forEach((b) => {
    b.addEventListener('click', () => {
      const hours = parseFloat(b.dataset.hours);
      if (!Number.isFinite(hours)) return;
      const r = commitTime(state.mjd + hours / 24, { animate: true, source: 'step' });
      if (r.clamped) toast('已到时间轴边界', 2400);
    });
  });

  $('win-days').addEventListener('change', (e) => {
    applyWindowDays(parseFloat(e.target.value));
  });

  document.querySelectorAll('.speed .sbtn').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.speed .sbtn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.speed = parseFloat(b.dataset.speed);
      if (state.playing) scheduleNextPlayback();
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
      analysis_window: {
        t0: state.t0,
        t1: state.t1,
        days: state.windowDays,
        purpose: 'analysis_filter',
      },
      coverage: { frames: hitStats.frames, exposures: hitStats.exposures, nights: hitStats.nights, radius_deg: HIT_RADIUS_DEG },
    };
    copy(JSON.stringify(payload, null, 2), '天区 JSON');
  });

  $('btn-link').addEventListener('click', () => copy(location.href, '链接'));

  $('btn-movescope').addEventListener('click', () => openMoveScope(moveScopeUrl()));

  window.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlayback(); }
    else if (e.key === 'ArrowLeft') { commitTime(state.mjd - (e.shiftKey ? 1 : 0.05), { animate: true, source: 'key' }); }
    else if (e.key === 'ArrowRight') { commitTime(state.mjd + (e.shiftKey ? 1 : 0.05), { animate: true, source: 'key' }); }
    else if (e.key === 'n' || e.key === 'N') goNow();
  });

  // 标签投影循环；时间推进由观测序列调度器负责，不再在 RAF 里改 state.mjd。
  function tick() {
    requestAnimationFrame(tick);
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
        <span style="color:#d7e8f8">cd 系统/视图工具 &amp;&amp; ./start.sh</span>（默认 8830）<br>
        然后访问 <span style="color:#d7e8f8">http://127.0.0.1:8830/</span>
      </div>`;
}

boot().catch(fatal);
