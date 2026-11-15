// modules.js — evidence modules inside Sky View.
// SkyBoT is a live external query; the band atlas is an instrument plan;
// 3D is the actual Sky View globe plus a clearly-labelled legacy link.
// The modules read the current selection from the existing Sky View DOM.

import { getLang } from './i18n.js';

const REPO = 'https://github.com/Beltranpcb/HuellaX';
const SKYBOT = 'https://ssp.imcce.fr/webservices/skybot/api/conesearch.php';
const MOLECULES = [
  { key: 'h2o', en: 'Water ice', es: 'Hielo de agua', lo: 2.70, hi: 3.10 },
  { key: 'co2', en: 'CO₂', es: 'CO₂', lo: 4.15, hi: 4.35 },
  { key: 'co', en: 'CO', es: 'CO', lo: 4.55, hi: 4.75 },
  { key: 'ch4', en: 'Methane', es: 'Metano', lo: 3.20, hi: 3.40 },
  { key: 'sil', en: 'Silicates', es: 'Silicatos', lo: 0.95, hi: 1.05 },
  { key: 'sil2', en: 'Silicate 2', es: 'Silicato 2', lo: 1.90, hi: 2.10 }
];

const STR = {
  en: {
    dock: 'Evidence modules', skybot: 'SkyBoT', bands: 'Bands', three: '3D',
    skybotKicker: 'LIVE · IMCCE',
    skybotTitle: 'Known solar-system objects',
    skybotDesc: 'Cone search around the current Sky View selection. External ephemerides; a non-match is not a discovery.',
    ra: 'RA (deg)', dec: 'Dec (deg)', date: 'UTC date', radius: 'Radius (arcsec)',
    query: 'Query SkyBoT', open: 'Open SkyBoT ↗', selection: 'Use current selection',
    idle: 'Ready. Results come from IMCCE, not from this project.',
    loading: 'Querying SkyBoT (IMCCE)…',
    empty: 'No known object in this cone. That is not evidence for a new object.',
    error: 'The live query could not be read from this browser (network or CORS). Open the query directly.',
    results: '{n} known object(s). Separation is recomputed against the selection.',
    obj: 'Object', cls: 'Class', sep: 'Sep.', err: 'Ephem. error',
    molActive: '{name}: {lo}–{hi} µm expected window, not a detection.',
    bandsKicker: 'INSTRUMENT PLAN', bandsTitle: '102 bands · 0.75–5.0 µm',
    bandsDesc: 'The band plan and common molecular windows. This is not a measured spectrum; measured spectra stay in MoveScope when the pipeline has one.',
    bandsIdle: 'Select a molecule to highlight its expected window.',
    spectralLink: 'Open MoveScope analysis ↗', legacySpectral: 'Legacy spectral notes',
    threeKicker: 'ACTUAL 3D CONTEXT', threeTitle: 'Where the selection sits',
    threeDesc: 'The globe behind this panel is the actual Sky View context. The separate 3D spectral page is legacy and must not be read as a measured 3D spectrum.',
    raLabel: 'RA', decLabel: 'DEC', fovLabel: 'FOV', hitsLabel: 'Local hits',
    threeOpen: 'Open legacy 3D ↗', repo: 'Project repository'
  },
  es: {
    dock: 'Módulos de evidencia', skybot: 'SkyBoT', bands: 'Bandas', three: '3D',
    skybotKicker: 'EN VIVO · IMCCE',
    skybotTitle: 'Objetos conocidos del sistema solar',
    skybotDesc: 'Búsqueda de cono alrededor de la selección actual de Sky View. Efemérides externas; un no_match no es un descubrimiento.',
    ra: 'AR (grados)', dec: 'Dec (grados)', date: 'Fecha UTC', radius: 'Radio (arcsec)',
    query: 'Consultar SkyBoT', open: 'Abrir SkyBoT ↗', selection: 'Usar selección actual',
    idle: 'Listo. Los resultados vienen de IMCCE, no de este proyecto.',
    loading: 'Consultando SkyBoT (IMCCE)…',
    empty: 'Ningún objeto conocido en este cono. Eso no es evidencia de un objeto nuevo.',
    error: 'La consulta en vivo no se pudo leer desde este navegador (red o CORS). Abre la consulta directamente.',
    results: '{n} objeto(s) conocido(s). La separación se recalcula con la selección.',
    obj: 'Objeto', cls: 'Clase', sep: 'Sep.', err: 'Error efem.',
    molActive: '{name}: {lo}–{hi} µm ventana esperada, no una detección.',
    bandsKicker: 'PLAN DEL INSTRUMENTO', bandsTitle: '102 bandas · 0,75–5,0 µm',
    bandsDesc: 'El plan de bandas y ventanas moleculares comunes. No es un espectro medido; los espectros medidos permanecen en MoveScope cuando el pipeline los tiene.',
    bandsIdle: 'Selecciona una molécula para resaltar su ventana esperada.',
    spectralLink: 'Abrir análisis en MoveScope ↗', legacySpectral: 'Notas espectrales legacy',
    threeKicker: 'CONTEXTO 3D REAL', threeTitle: 'Dónde está la selección',
    threeDesc: 'El globo detrás de este panel es el contexto real de Sky View. La página 3D espectral separada es legacy y no debe leerse como un espectro 3D medido.',
    raLabel: 'AR', decLabel: 'DEC', fovLabel: 'FOV', hitsLabel: 'Impactos locales',
    threeOpen: 'Abrir 3D legacy ↗', repo: 'Repositorio del proyecto'
  }
};

const state = { lang: getLang(), open: null, mol: null, bits: [] };

function T(key) { return (STR[state.lang] && STR[state.lang][key]) || STR.en[key] || key; }
function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function readSelection() {
  const ra = Number($('in-ra')?.value);
  const dec = Number($('in-dec')?.value);
  const fov = Number($('in-fov')?.value);
  const mjdText = $('clock-mjd')?.textContent || '';
  const m = mjdText.match(/MJD\s+([0-9.]+)/i);
  const mjd = m ? Number(m[1]) : null;
  const date = mjd !== null ? new Date((mjd - 40587) * 86400000).toISOString().slice(0, 10) : new Date().toISOString().slice(0, 10);
  return { ra: Number.isFinite(ra) ? ra : 0, dec: Number.isFinite(dec) ? dec : 0, fov: Number.isFinite(fov) ? fov : 0, mjd, date };
}
function mjdFromDate(value) {
  const d = new Date(value + 'T00:00:00Z');
  return Number.isNaN(d.getTime()) ? null : d.getTime() / 86400000 + 40587;
}
function skybotUrl(sel) {
  if (sel.mjd === null) return null;
  const u = new URL(SKYBOT);
  u.searchParams.set('-ep', (sel.mjd + 2400000.5).toFixed(6));
  u.searchParams.set('-ra', sel.ra.toFixed(6));
  u.searchParams.set('-dec', sel.dec.toFixed(6));
  u.searchParams.set('-rd', (60 / 3600).toFixed(6));
  u.searchParams.set('-mime', 'json');
  u.searchParams.set('-loc', '500');
  u.searchParams.set('-objFilter', '111');
  u.searchParams.set('-refsys', 'EQJ2000');
  return u;
}
function human(hms) {
  const m = String(hms || '').trim().match(/^([+-]?)(\d+)\D+(\d+)\D+(\d+(?:\.\d+)?)/);
  if (!m) return null;
  const v = Number(m[2]) + Number(m[3]) / 60 + Number(m[4]) / 3600;
  return m[1] === '-' ? -v : v;
}
function separation(ra1, dec1, ra2, dec2) {
  const p1 = ra1 * Math.PI / 180, p2 = ra2 * Math.PI / 180;
  const d1 = dec1 * Math.PI / 180, d2 = dec2 * Math.PI / 180;
  const c = Math.sin(d1) * Math.sin(d2) + Math.cos(d1) * Math.cos(d2) * Math.cos(p1 - p2);
  return Math.acos(Math.min(1, Math.max(-1, c))) * 180 / Math.PI * 3600;
}

function buildChrome() {
  if ($('hx-dock')) return;
  const dock = document.createElement('div');
  dock.id = 'hx-dock'; dock.className = 'hx-dock';
  dock.innerHTML = `
    <button type="button" class="hx-dock-btn skybot" data-hx="skybot"><i></i><span>${T('skybot')}</span></button>
    <button type="button" class="hx-dock-btn bands" data-hx="bands"><i></i><span>${T('bands')}</span></button>
    <button type="button" class="hx-dock-btn three" data-hx="three"><i></i><span>${T('three')}</span></button>`;
  document.body.appendChild(dock);
  const drawer = document.createElement('aside');
  drawer.id = 'hx-drawer'; drawer.className = 'hx-drawer'; drawer.setAttribute('aria-hidden', 'true');
  drawer.innerHTML = `<div class="hx-drawer-head"><div><div class="hx-kicker" id="hx-kicker"></div><h2 id="hx-title"></h2><p id="hx-desc"></p></div><button class="hx-close" id="hx-close" aria-label="Close">×</button></div><div class="hx-drawer-body" id="hx-body"></div>`;
  document.body.appendChild(drawer);
  dock.querySelectorAll('[data-hx]').forEach((b) => b.addEventListener('click', () => openModule(b.dataset.hx)));
  $('hx-close').addEventListener('click', closeModule);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModule(); });
  document.querySelectorAll('.lang-btn[data-lang]').forEach((b) => b.addEventListener('click', () => {
    setTimeout(() => { state.lang = getLang(); renderDock(); if (state.open) openModule(state.open, true); }, 30);
  }));
}

function renderDock() {
  const dock = $('hx-dock');
  if (!dock) return;
  dock.querySelectorAll('[data-hx]').forEach((b) => {
    const key = b.dataset.hx;
    b.querySelector('span').textContent = T(key);
    b.classList.toggle('active', state.open === key);
  });
}
function setHead(kicker, title, desc) {
  $('hx-kicker').textContent = kicker;
  $('hx-title').textContent = title;
  $('hx-desc').textContent = desc;
}
function closeModule() {
  state.open = null;
  $('hx-drawer')?.classList.remove('open');
  $('hx-drawer')?.setAttribute('aria-hidden', 'true');
  renderDock();
}
function openModule(name, rerender = false) {
  if (!name) return closeModule();
  state.open = name;
  const drawer = $('hx-drawer');
  drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false');
  renderDock();
  const sel = readSelection();
  if (name === 'skybot') renderSkybot(sel);
  else if (name === 'bands') renderBands();
  else renderThree(sel);
  if (!rerender) drawer.querySelector('.hx-drawer-body').scrollTop = 0;
}

function renderSkybot(sel) {
  setHead(T('skybotKicker'), T('skybotTitle'), T('skybotDesc'));
  const body = $('hx-body');
  body.innerHTML = `
    <div class="hx-form">
      <div class="hx-field"><label for="hx-ra">${T('ra')}</label><input id="hx-ra" type="number" min="0" max="360" step="0.000001" value="${sel.ra.toFixed(6)}"></div>
      <div class="hx-field"><label for="hx-dec">${T('dec')}</label><input id="hx-dec" type="number" min="-90" max="90" step="0.000001" value="${sel.dec.toFixed(6)}"></div>
      <div class="hx-field"><label for="hx-date">${T('date')}</label><input id="hx-date" type="date" value="${sel.date}"></div>
      <div class="hx-field"><label for="hx-radius">${T('radius')}</label><input id="hx-radius" type="number" min="10" max="600" step="1" value="60"></div>
    </div>
    <div class="hx-actions">
      <button type="button" class="hx-btn primary" id="hx-query">${T('query')}</button>
      <a class="hx-btn" id="hx-open" href="https://ssp.imcce.fr/webservices/skybot/" target="_blank" rel="noopener">${T('open')}</a>
      <button type="button" class="hx-btn" id="hx-use">${T('selection')}</button>
    </div>
    <div class="hx-url" id="hx-url">—</div>
    <div class="hx-status" id="hx-status">${T('idle')}</div>
    <div class="hx-repo">${T('repo')} <a href="${REPO}" target="_blank" rel="noopener">Beltranpcb/HuellaX ↗</a></div>`;
  const upd = () => {
    const s = readSelection();
    s.ra = Number($('hx-ra').value); s.dec = Number($('hx-dec').value);
    const d = mjdFromDate($('hx-date').value);
    s.mjd = Number.isFinite(d) ? d : null;
    const u = skybotUrl(s);
    $('hx-url').textContent = u ? u.toString() : '—';
    $('hx-open').href = u ? u.toString() : 'https://ssp.imcce.fr/webservices/skybot/';
  };
  ['hx-ra', 'hx-dec', 'hx-date'].forEach((id) => $(id).addEventListener('input', upd));
  $('hx-use').addEventListener('click', () => {
    const s = readSelection();
    $('hx-ra').value = s.ra.toFixed(6); $('hx-dec').value = s.dec.toFixed(6); $('hx-date').value = s.date;
    upd();
    $('hx-status').className = 'hx-status'; $('hx-status').textContent = T('idle');
  });
  upd();
  $('hx-query').addEventListener('click', async () => {
    const s = readSelection();
    s.ra = Number($('hx-ra').value); s.dec = Number($('hx-dec').value);
    const d = mjdFromDate($('hx-date').value); s.mjd = Number.isFinite(d) ? d : null;
    const u = skybotUrl(s);
    if (!u) return;
    const status = $('hx-status');
    status.className = 'hx-status'; status.textContent = T('loading');
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 8000);
      const r = await fetch(u.toString(), { signal: controller.signal });
      clearTimeout(timer);
      if (r.status === 204) { status.textContent = T('empty'); return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      if (!Array.isArray(data) || !data.length) { status.textContent = T('empty'); return; }
      const rows = data.map((row) => {
        const ra = human(row['RA (hms)']), dec = human(row['DEC (dms)']);
        return { row, sep: Number.isFinite(ra) && Number.isFinite(dec) ? separation(s.ra, s.dec, ra * 15, dec) : null };
      }).sort((a, b) => (a.sep ?? 1e9) - (b.sep ?? 1e9));
      status.textContent = T('results').replace('{n}', String(data.length));
      const table = document.createElement('table'); table.className = 'hx-table';
      table.innerHTML = `<thead><tr><th>${T('obj')}</th><th>${T('cls')}</th><th>${T('sep')}</th><th>${T('err')}</th></tr></thead><tbody></tbody>`;
      const tb = table.querySelector('tbody');
      rows.slice(0, 12).forEach(({ row, sep }) => {
        const tr = document.createElement('tr');
        const name = row.Num ? `(${row.Num}) ${row.Name || ''}` : (row.Name || '—');
        tr.innerHTML = `<td>${esc(name)}</td><td>${esc(row.Class || '—')}</td><td>${sep === null ? '—' : sep.toFixed(1) + '″'}</td><td>${Number.isFinite(Number(row['Err (arcsec)'])) ? Number(row['Err (arcsec)']).toFixed(2) + '″' : '—'}</td>`;
        tb.appendChild(tr);
      });
      status.appendChild(table);
    } catch (err) {
      status.className = 'hx-status error';
      status.textContent = T('error') + ' ';
      const a = document.createElement('a'); a.href = u.toString(); a.target = '_blank'; a.rel = 'noopener'; a.textContent = T('open');
      status.appendChild(a);
    }
  });
}

function drawBands() {
  const c = $('hx-bands'); if (!c) return;
  const dpr = Math.min(devicePixelRatio || 1, 2), r = c.getBoundingClientRect();
  c.width = Math.max(1, Math.round(r.width * dpr)); c.height = Math.max(1, Math.round(r.height * dpr));
  const ctx = c.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = r.width, h = r.height, pad = 6, top = 12, bottom = 16, pw = w - pad * 2, ph = h - top - bottom;
  ctx.clearRect(0, 0, w, h);
  const mol = state.mol;
  if (mol) {
    const x1 = pad + ((mol.lo - .75) / 4.25) * pw, x2 = pad + ((mol.hi - .75) / 4.25) * pw;
    ctx.fillStyle = 'rgba(182,156,255,.14)'; ctx.fillRect(x1, top, Math.max(1, x2 - x1), ph);
    ctx.strokeStyle = 'rgba(182,156,255,.6)'; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x1, top); ctx.lineTo(x1, top + ph); ctx.moveTo(x2, top); ctx.lineTo(x2, top + ph); ctx.stroke(); ctx.setLineDash([]);
  }
  for (let i = 0; i < 102; i++) {
    const x = pad + (i / 102) * pw, bw = Math.max(1, pw / 102 - .6);
    const lambda = .75 + (i / 101) * 4.25;
    const inMol = mol && lambda >= mol.lo && lambda <= mol.hi;
    ctx.fillStyle = inMol ? '#b69cff' : (i < 34 ? 'rgba(87,230,255,.55)' : i < 68 ? 'rgba(143,233,198,.46)' : 'rgba(255,180,84,.52)');
    ctx.fillRect(x, top + ph * .2, bw, ph * .6);
  }
  ctx.fillStyle = 'rgba(126,148,170,.75)'; ctx.font = '7px ui-monospace, monospace';
  [0.75, 1.5, 2.5, 3.5, 5].forEach((v) => {
    const x = pad + ((v - .75) / 4.25) * pw;
    ctx.fillRect(x, top + ph, 1, 4);
    ctx.fillText(v.toFixed(2), Math.min(w - 20, Math.max(pad, x - 7)), h - 3);
  });
  ctx.fillStyle = 'rgba(126,148,170,.55)'; ctx.fillText('µm', w - pad - 11, top - 3);
}

function renderBands() {
  setHead(T('bandsKicker'), T('bandsTitle'), T('bandsDesc'));
  const body = $('hx-body');
  body.innerHTML = `
    <canvas id="hx-bands" aria-label="SPHEREx 102-band instrument plan"></canvas>
    <div class="hx-band-read"><span id="hx-band-read">0.75–5.0 µm · 102 bands</span><b>schematic band plan</b></div>
    <div class="hx-mols" id="hx-mols"></div>
    <div class="hx-mol-read" id="hx-mol-read">${state.mol ? T('molActive').replace('{name}', state.mol[state.lang] || state.mol.en).replace('{lo}', state.mol.lo.toFixed(2)).replace('{hi}', state.mol.hi.toFixed(2)) : T('bandsIdle')}</div>
    <div class="hx-actions">
      <a class="hx-btn" href="http://127.0.0.1:8790/" target="_blank" rel="noopener">${T('spectralLink')}</a>
      <a class="hx-btn" href="../../../astro/modulos/m6_huella_espectral/LEEME.md" target="_blank" rel="noopener">${T('legacySpectral')}</a>
    </div>
    <div class="hx-repo">${T('repo')} <a href="${REPO}" target="_blank" rel="noopener">Beltranpcb/HuellaX ↗</a></div>`;
  const mols = $('hx-mols');
  MOLECULES.forEach((m) => {
    const b = document.createElement('button'); b.type = 'button';
    b.className = 'hx-mol' + (state.mol && state.mol.key === m.key ? ' active' : '');
    b.textContent = m[state.lang] || m.en;
    b.addEventListener('click', () => {
      state.mol = state.mol && state.mol.key === m.key ? null : m;
      renderBands();
    });
    mols.appendChild(b);
  });
  const c = $('hx-bands');
  c.addEventListener('mousemove', (e) => {
    const r = c.getBoundingClientRect(), x = e.clientX - r.left - 6, pw = r.width - 12;
    if (x < 0 || x > pw) return;
    const i = Math.min(101, Math.max(0, Math.floor((x / pw) * 102)));
    $('hx-band-read').textContent = `Band ${String(i + 1).padStart(3, '0')} · ${(.75 + (i / 101) * 4.25).toFixed(3)} µm`;
  });
  c.addEventListener('mouseleave', () => { $('hx-band-read').textContent = '0.75–5.0 µm · 102 bands'; });
  drawBands();
  const ro = new ResizeObserver(() => drawBands());
  ro.observe(c);
}

function renderThree(sel) {
  setHead(T('threeKicker'), T('threeTitle'), T('threeDesc'));
  const hits = $('hits-count')?.textContent || '—';
  const body = $('hx-body');
  body.innerHTML = `
    <div class="hx-ctx">
      <div><span>${T('raLabel')}</span><b>${sel.ra.toFixed(5)}°</b></div>
      <div><span>${T('decLabel')}</span><b>${sel.dec.toFixed(5)}°</b></div>
      <div><span>${T('fovLabel')}</span><b>${sel.fov.toFixed(2)}°</b></div>
      <div><span>${T('hitsLabel')}</span><b>${esc(hits)}</b></div>
    </div>
    <div class="hx-actions">
      <a class="hx-btn" href="../../../astro/modulos/m9_sitio/espectro3d.html" target="_blank" rel="noopener">${T('threeOpen')}</a>
    </div>
    <div class="hx-repo">${T('repo')} <a href="${REPO}" target="_blank" rel="noopener">Beltranpcb/HuellaX ↗</a></div>`;
}

buildChrome();
renderDock();
