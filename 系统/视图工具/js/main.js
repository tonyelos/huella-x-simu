// main.js — 状态、UI 绑定、数据管线
import { createGlobe } from './globe.js';
import { createSkyMap } from './skymap.js';
import * as A from './astro.js';
import { initI18n, tr, getLang, setLang, targetLabel } from './i18n.js';

const $ = (id) => document.getElementById(id);

// English is the primary language; ?lang=es / localStorage can switch to Spanish.
initI18n();

// MoveScope 前端地址（app/server.py 提供，默认 8790）。
// 可在页面加载前用 window.MOVESCOPE_ORIGIN 覆盖（部署/联调时不必改源码）。
const MOVESCOPE_ORIGIN = String(
  window.MOVESCOPE_ORIGIN || 'http://127.0.0.1:8790'
).replace(/\/+$/, '');
const PRESET_FOV = { barnard: 0.5, epsind_b: 0.5, wise0855: 0.2, rw_aur: 0.5, r_dra: 0.5, m101: 0.1, campo_a: 0.1 };
// 分析层时间窗口：以当前数据时相为中心，过滤 MoveScope 分析层历元范围。
const WINDOW_OPTIONS = [30, 90, 180, 365, 730];
const DEFAULT_WINDOW_DAYS = 365;

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
  windowDays: DEFAULT_WINDOW_DAYS,
  t0: 0,
  t1: 0,
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
    toast(ok ? tr('copy.ok', { label }) : tr('copy.fail', { label }), ok ? 2400 : 3600);
  }
  return ok;
}

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

// ---------------------------------------------------------------- 语言 / i18n
const fmtInt = (n) => Number(n).toLocaleString('en-US');

/** Refresh the clock/MJD/mode and UTC input from state (no time mutation). */
function syncClockUI() {
  $('clock-date').textContent = `${A.fmtUtc(state.mjd)} UTC`;
  $('clock-mjd').textContent = `MJD ${state.mjd.toFixed(4)}`;
  $('clock-mode').textContent = state.mode === 'now' ? tr('clock.mode.now') : tr('clock.mode.data');
  const utcInput = $('in-utc');
  if (utcInput) utcInput.value = A.fmtUtc(state.mjd).slice(0, 16);
}

function syncTrackNote() {
  const el = $('tracknote');
  if (!el) return;
  if (!fields.length) {
    el.textContent = tr('tracknote.default');
    return;
  }
  const near = fields.some((f) => Math.abs(f.mjd - state.mjd) < 0.5);
  el.textContent = near ? tr('tracknote.default') : tr('tracknote.none');
}

function syncPlayButton() {
  const el = $('btn-play');
  if (!el) return;
  const label = state.playing ? tr('pause.aria') : tr('play.aria');
  el.setAttribute('aria-label', label);
  el.title = label;
}

function syncDatasetChips() {
  if (!fields.length) return;
  $('chip-count').textContent = tr('chip.count', {
    frames: fmtInt(fields.length),
    exposures: fmtInt(nExposures),
  });
  $('chip-range').textContent = `${A.fmtUtc(mjdMin).slice(0, 10)} → ${A.fmtUtc(mjdMax).slice(0, 10)}`;
}

function syncDataNote() {
  if (!fields.length) return;
  const el = $('data-note');
  if (!el) return;
  el.innerHTML = tr('data.note', { frames: fmtInt(fields.length), exposures: fmtInt(nExposures) });
}

/** Sky-map target captions live in canvas state; refresh their `short` labels. */
function syncTargetShortLabels() {
  for (const target of targets) target.short = targetLabel(target);
  sky?.redraw();
}

/** Switch EN/ES without changing region, time, playback or handoff parameters. */
function switchLang(next) {
  if (!setLang(next)) return;
  // Do not leave a transient toast from the previous language on screen.
  clearTimeout(toastTimer);
  $('toast').classList.remove('show');
  syncClockUI();
  syncTrackNote();
  syncPlayButton();
  syncDatasetChips();
  syncDataNote();
  syncTargetShortLabels();
  syncRegionUI();
  syncWindowUI();
  updateLabels();
  clearTimeout(urlTimer);
  writeUrl();
}

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
  const hemi = site.lon >= 0 ? tr('hemi.e') : tr('hemi.w');
  $('ro-site').textContent = `${A.fmtDec(site.lat)}  ${hemi}${Math.abs(site.lon).toFixed(1)}°`;
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
  $('hits-count').textContent = tr('hits.count', {
    frames,
    exposures,
    nights,
  });

  const box = $('hits');
  box.innerHTML = '';

  const target = matchedTarget();
  const curated = target ? epochIndex[target.id] || [] : [];
  const cEl = $('curated');
  if (curated.length) {
    cEl.hidden = false;
    const near = curated.reduce((b, e) => (Math.abs(e.mjd - state.mjd) < Math.abs(b.mjd - state.mjd) ? e : b), curated[0]);
    cEl.innerHTML = tr('curated', { n: curated.length, date: A.fmtUtc(near.mjd).slice(0, 10) });
    cEl.onclick = () => {
      setRegion({ ra: target.ra, dec: target.dec }, { focus: true, recenter: true });
      commitTime(near.mjd, 'data', { animate: true, source: 'curated' });
    };
  } else {
    cEl.hidden = true;
    cEl.onclick = null;
  }

  if (!hitStats.groups.length) {
    const d = document.createElement('div');
    d.className = 'empty';
    d.innerHTML = tr('hits.empty', {
      radius: HIT_RADIUS_DEG,
      weeks: nWeeks,
      frames: fmtInt(fields.length),
      exposures: fmtInt(nExposures),
    });
    box.appendChild(d);
    return;
  }

  for (const g of hitStats.groups.slice(0, 40)) {
    const row = document.createElement('div');
    row.className = 'hit';
    row.innerHTML = `<span class="id">${g.obsid} <i>· ${tr('hit.frames', { n: g.n })}</i></span><span class="sep"><b>${(g.sep * 60).toFixed(1)}′</b></span>`;
    row.title = tr('hit.title', { utc: A.fmtUtc(g.mjd), n: g.n });
    row.addEventListener('click', () => {
      setRegion({ ra: g.ra, dec: g.dec }, { focus: true, recenter: true });
      commitTime(g.mjd, 'data', { animate: true, source: 'hit' });
      toast(tr('hit.toast', { obsid: g.obsid }));
    });
    box.appendChild(row);
  }
}

function updatePresetState() {
  const t = matchedTarget();
  document.querySelectorAll('.preset[data-id]').forEach((el) => {
    const target = targets.find((x) => x.id === el.dataset.id);
    if (target) el.textContent = targetLabel(target);
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
    const copied = await copy(url, tr('copy.label.movescope'), { silent: true });
    toast(copied ? tr('movescope.blocked.copied') : tr('movescope.blocked.manual'), 4200);
    return;
  }
  if (win) win.close();
  const copied = await copy(url, tr('copy.label.movescope'), { silent: true });
  toast(
    copied
      ? tr('movescope.unreachable.copied', { origin: MOVESCOPE_ORIGIN })
      : tr('movescope.unreachable', { origin: MOVESCOPE_ORIGIN }),
    5200,
  );
}

function updateMoveScopeHint() {
  const t = matchedTarget();
  const el = $('ms-hint');
  if (!el) return;
  el.classList.remove('warn');
  if (t) {
    el.innerHTML = tr('movescope.target', { name: targetLabel(t) });
  } else {
    el.innerHTML = tr('movescope.custom');
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
      opt.textContent = tr('window.custom', { days: state.windowDays.toFixed(1) });
    }
    sel.value = value;
  }
  updateAnalysisHint();
}

function updateAnalysisHint() {
  const el = $('analysis-hint');
  if (!el) return;
  let html = tr('analysis.hint');
  if (state.fov > 3.0) html += tr('analysis.fovWarn', { fov: state.fov.toFixed(2) });
  el.innerHTML = html;
}

// ---------------------------------------------------------------- 时间（唯一入口）
/**
 * 所有时间入口的唯一提交点。
 * 同步 state.mjd、时钟/MJD、滑杆、UTC 输入、分析窗口、URL、地球角度。
 */
function commitTime(mjd, mode = 'data', { animate = false, source = 'set', silent = false, allowOutside = false } = {}) {
  if (!Number.isFinite(mjd)) return { mjd: state.mjd, clamped: false };
  const bounded = clamp(mjd, mjdMin, mjdMax);
  const next = allowOutside ? mjd : bounded;
  state.mjd = next;
  state.mode = mode === 'now' ? 'now' : 'data';
  state.t0 = next - state.windowDays;
  state.t1 = next + state.windowDays;

  globe?.setClock(next, { animate });
  syncClockUI();
  const frac = (next - mjdMin) / (mjdMax - mjdMin);
  $('time').value = String(clamp(frac * 1000, 0, 1000));

  const site = updateSite();
  updateLabels(site);
  syncTrackNote();
  syncWindowUI();
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
    toast(tr('utc.invalid'), 3600);
    return;
  }
  const r = commitTime(mjd, 'data', { animate: true, source: 'utc' });
  if (r.clamped) {
    toast(tr('utc.clamped', { utc: A.fmtUtc(r.mjd).slice(0, 16) }), 3600);
  }
}

function setPlaying(on) {
  state.playing = on;
  $('btn-play').classList.toggle('playing', on);
  syncPlayButton();
  if (!on) {
    syncWindowUI();
    scheduleUrl();
  }
}

function togglePlayback() {
  if (state.playing) {
    setPlaying(false);
    return;
  }
  // 旧行为：从末尾/now 起播时先回到 mjdMin。
  if (state.mode === 'now' || state.mjd > mjdMax - 0.6) {
    commitTime(mjdMin, 'data', { silent: true, animate: false, source: 'play-reset' });
  }
  setPlaying(true);
}

function goNow() {
  const now = A.dateToMjd(new Date());
  setPlaying(false);
  commitTime(now, 'now', { animate: true, source: 'now', allowOutside: true });
  if (Math.abs(now - mjdMax) > 40) {
    toast(tr('now.noTrack', { date: A.fmtUtc(mjdMax).slice(0, 10) }), 3200);
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
    ? `${targetLabel(t)} · ${state.fov.toFixed(2)}°`
    : `${A.fmtRa(state.ra)} ${A.fmtDec(state.dec)} · ${state.fov.toFixed(2)}°`;

  elSite.style.left = `${leftPct.toFixed(2)}%`;
  elSite.style.top = `${topPct.toFixed(2)}%`;
  elSite.style.transform = `translate(-50%, ${m.halfPx + 12}px)`;
  $('lb-site-val').textContent = tr('globe.site.value', {
    dec: A.fmtDec(site.lat),
    hemi: site.lon >= 0 ? tr('hemi.e') : tr('hemi.w'),
    lon: Math.abs(site.lon).toFixed(1),
  });
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
  p.set('lang', getLang());
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
  const [fieldsDoc, targetsDoc, epochDoc] = await Promise.all([
    fetch('./data/fields.json').then((r) => r.json()),
    fetch('./data/targets.json').then((r) => r.json()),
    fetch('./data/epoch_index.json').then((r) => r.json()).catch(() => ({ targets: {} })),
  ]);

  fields = fieldsDoc.fields
    .map(([obsid, det, ra, dec, mjd, lat, lon, elon, elat, xposure]) => ({
      obsid, det, ra, dec, mjd, lat, lon, elon, elat, xposure,
    }))
    .sort((a, b) => a.mjd - b.mjd);

  targets = (targetsDoc.targets || []).map((target) => ({ ...target, short: targetLabel(target) }));
  epochIndex = epochDoc.targets || {};
  mjdMin = fields[0].mjd;
  mjdMax = fields[fields.length - 1].mjd;

  const nExp = new Set(fields.map((f) => f.obsid)).size;
  nExposures = nExp;
  nWeeks = new Set(fields.map((f) => f.obsid.split('_').slice(0, 2).join('_'))).size;
  syncDatasetChips();
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
  syncTrackNote();
  syncDataNote();

  const presetRow = $('presets');
  for (const target of targets) {
    const b = document.createElement('button');
    b.className = 'preset';
    b.dataset.id = target.id;
    b.textContent = targetLabel(target);
    b.title = `RA ${target.ra}  DEC ${target.dec}`;
    b.addEventListener('click', () => {
      setRegion({ ra: target.ra, dec: target.dec, fov: PRESET_FOV[target.id] ?? 0.5 }, { focus: true, recenter: true, rezoom: true });
      const curated = epochIndex[target.id] || [];
      if (curated.length) {
        const near = curated.reduce((b2, e) => (Math.abs(e.mjd - state.mjd) < Math.abs(b2.mjd - state.mjd) ? e : b2), curated[0]);
        commitTime(near.mjd, 'data', { animate: true, source: 'preset' });
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
      el.innerHTML = `<span>${tr('cursor.label')}</span> RA ${A.fmtRa(s.ra)} <i>·</i> DEC ${A.fmtDec(s.dec)}`;
    },
    onPick: (lat, lon) => {
      const s = A.skyOverhead(lat, lon, state.mjd);
      setRegion({ ra: s.ra, dec: s.dec }, { recenter: true });
      toast(tr('globe.pick', { ra: A.fmtRa(s.ra), dec: A.fmtDec(s.dec) }));
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
  commitTime(startMjd, 'data', { animate: false, source: 'boot', silent: true });
  syncRegionUI();
  drawDensity();
  writeUrl();

  // 交互
  document.querySelectorAll('.lang-btn[data-lang]').forEach((b) => {
    b.addEventListener('click', () => switchLang(b.dataset.lang));
  });

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
    commitTime(mjdMin + frac * (mjdMax - mjdMin), 'data', { animate: true, source: 'slider' });
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
      const r = commitTime(state.mjd + hours / 24, 'data', { animate: true, source: 'step' });
      if (r.clamped) toast(tr('step.edge'), 2400);
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
    copy(JSON.stringify(payload, null, 2), tr('copy.label.json'));
  });

  $('btn-link').addEventListener('click', () => copy(location.href, tr('copy.label.link')));

  $('btn-movescope').addEventListener('click', () => openMoveScope(moveScopeUrl()));

  window.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlayback(); }
    else if (e.key === 'ArrowLeft') { commitTime(state.mjd - (e.shiftKey ? 1 : 0.05), 'data', { animate: true, source: 'key' }); }
    else if (e.key === 'ArrowRight') { commitTime(state.mjd + (e.shiftKey ? 1 : 0.05), 'data', { animate: true, source: 'key' }); }
    else if (e.key === 'n' || e.key === 'N') goNow();
  });

  // 连续时间播放：时间推进也由 commitTime 统一提交，播放期间不做地球 tween。
  let last = performance.now();
  function tick(now) {
    requestAnimationFrame(tick);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (state.playing) {
      let m = state.mjd + dt * state.speed;
      if (m > mjdMax) m = mjdMin;
      commitTime(m, 'data', { silent: true, animate: false, source: 'play' });
    } else {
      updateLabels();
    }
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
  const body = isWebgl
    ? tr('fatal.webgl')
    : tr('fatal.load', {
      msg,
      cmd: './start.sh',
      url: 'http://127.0.0.1:8830/',
    });
  l.innerHTML = `<div style="max-width:440px;text-align:center;font-family:ui-monospace,monospace;font-size:11px;line-height:1.9;color:#ffb454">${body}</div>`;
}

boot().catch(fatal);
