// i18n.js — English/Spanish UI strings, language selection and persistence.
//
// Contract:
//   - English is the default and the primary language.
//   - `?lang=en|es` (search first, hash fallback) overrides localStorage.
//   - localStorage key `spherex.view.lang` remembers the user's last choice.
//   - The header EN/ES switch calls setLang(); main.js re-renders dynamic text.
//
// This module is intentionally DOM-free at import time so tools/smoke.py can
// import DICTS with Node and verify that both dictionaries are complete.

export const DEFAULT_LANG = 'en';
export const LANGS = ['en', 'es'];
export const LANG_KEY = 'spherex.view.lang';

export const DICTS = {
  en: {
    // Document chrome
    'doc.title': 'SPHEREx Sky View · MoveScope',
    'doc.description': 'Interactive 3D globe and sky-region selector driven by real SPHEREx exposure-header data.',
    'chip.count': '{frames} frames / {exposures} exposures',

    // Brand / globe labels
    'brand.skyView': 'Sky View',
    'brand.kicker': 'SKY REGION VIEWER',
    'globe.aria': '3D globe',
    'label.site': 'Zenith site',
    'label.sky': 'Region center',
    'cursor.label': 'Cursor',
    'hemi.e': 'E',
    'hemi.w': 'W',

    // Header clock
    'now': 'Now',
    'now.title': 'Return to the actual current time',
    'clock.mode.data': 'Data epoch',
    'clock.mode.now': 'Live',

    // Left panel
    'panel.left.title': 'Sky region selector',
    'panel.left.sub': 'SKY REGION',
    'skymap.aria': 'All-sky region map',
    'skymap.minimap.title': 'All-sky minimap: click to jump',
    'skymap.tag': 'All-sky · drag = pan · wheel = zoom',
    'field.ra': 'RA',
    'field.dec': 'DEC',
    'field.fov': 'FOV',
    'unit.deg': 'deg',
    'toggle.track': 'Ground track',
    'toggle.cov': 'Exposure ground points',
    'toggle.mapcov': 'Sky coverage',
    'hint.main':
      'Click the globe to pick the <b>sky region at the zenith</b> at that moment; drag the sky box to move it, scroll to zoom, double-click to reset.<br/>' +
      'Space: play/pause · ←/→: time · N: now<br/>' +
      'Deep link: <span class="mono">?ra=269.452077&amp;dec=4.693365&amp;fov=0.5&amp;target=barnard</span>',

    // Right panel
    'panel.right.title': 'Selected region',
    'panel.right.sub': 'SELECTION',
    'kv.fov': 'Field of view',
    'kv.galactic': 'Galactic',
    'kv.site': 'Zenith site',
    'kv.lst': 'Local sidereal time',
    'hits.head': 'Local hits <em>2.47°</em>',
    'hits.count': '{frames} frames · {exposures} exposures · {nights} nights',
    'hit.frames': '{n} frames',
    'hit.title': '{utc} · {n} frames',
    'hit.toast': 'Moved to {obsid}',
    'curated': '<b>Curated epochs {n}</b> · epoch_catalog → {date}',
    'hits.empty':
      'No local hits within {radius}°.<br/>' +
      'This does not mean it was never observed: the local index is only a subset of {weeks} observing weeks ' +
      '({frames} frames / {exposures} exposures). Try another coverage strip or click “Now” for a different epoch.',
    'copy.json': 'Copy JSON',
    'copy.link': 'Copy link',
    'open.movescope': 'Open in MoveScope',
    'movescope.target':
      'Opening the <b>{name}</b> region; MoveScope analyzes by coordinates. You can switch modes after entering.',
    'movescope.custom':
      'Custom region; MoveScope analyzes by coordinates (REAL if real epochs exist, otherwise SIM). ' +
      'You can switch modes after entering.',
    'analysis.hint':
      'One handoff: in MoveScope switch between <b>Normal / Cascade-residual / Track</b>; ' +
      't0–t1 is the analysis filter range, not the data epoch.',
    'analysis.fovWarn':
      '<br><span style="color:var(--amber)">Current FOV {fov}° exceeds the 3.0° analysis limit in MoveScope; ' +
      'reduce the FOV first.</span>',
    'data.note':
      'Data: <span class="mono">data/fields.json</span> (frames table via ' +
      '<span class="mono">tools/build_field_index.py</span>) · ' +
      '{frames} frames / {exposures} exposures.',

    // Time bar
    'time.axis': 'UTC timeline',
    'time.utc.placeholder': 'YYYY-MM-DD HH:MM',
    'time.utc.aria': 'UTC time (YYYY-MM-DD HH:MM)',
    'time.go': 'Go',
    'time.playnote': 'Playback = continuous time',
    'time.slider.aria': 'Time position',
    'window.title': 'Analysis time window',
    'window.days': 'days',
    'window.days.aria': 'Analysis time window in days',
    'window.custom': 'Custom {days} days',
    'window.note': 'Window = <b>analysis-layer filter range</b>; not the data epoch / current time.',
    'speed': 'Speed',
    'tracknote.default': 'Track window ±12 h',
    'tracknote.none': 'No exposures within ±12 h of this epoch',
    'play.aria': 'Play',
    'pause.aria': 'Pause',
    'loader': 'Loading globe and exposure index…',

    // Copy / handoff / errors
    'copy.ok': '{label} copied',
    'copy.fail': 'Could not copy {label}; copy it manually.',
    'copy.label.json': 'Region JSON',
    'copy.label.link': 'Link',
    'copy.label.movescope': 'MoveScope link',
    'movescope.blocked.copied': 'The browser blocked the new tab; the MoveScope link has been copied.',
    'movescope.blocked.manual': 'The browser blocked the new tab; open MoveScope manually.',
    'movescope.unreachable.copied':
      'Cannot reach {origin}: start MoveScope (app/server.py) first. The link has been copied.',
    'movescope.unreachable': 'Cannot reach {origin}: start MoveScope (app/server.py) first.',
    'utc.invalid': 'UTC time must use YYYY-MM-DD HH:MM (UTC).',
    'utc.clamped': 'UTC time is outside the local index; moved to {utc} UTC.',
    'step.edge': 'Reached the end of the time axis.',
    'now.noTrack': 'The local index ends on {date}; there is no ground track for the live moment.',
    'globe.pick': 'Zenith region → RA {ra}  DEC {dec}',
    'globe.site.value': 'Zenith {dec} {hemi}{lon}°',

    // Fatal loader messages
    'fatal.webgl':
      'This browser does not support <b>WebGL2</b> (or it is disabled), so the 3D globe cannot start.<br/><br/>' +
      '<span style="color:#7d90a6">This is a graphics/browser setting, not a network problem. ' +
      'Open this page in a recent Chrome / Safari, or check the WebGL2 status at chrome://gpu.</span>',
    'fatal.load':
      'Failed to load: {msg}<br/><br/>' +
      'This page uses <span style="color:#d7e8f8">fetch</span> and ES modules, so it must be served over local HTTP:<br/>' +
      '<span style="color:#d7e8f8">{cmd}</span> (default 8830)<br/>' +
      'Then open <span style="color:#d7e8f8">{url}</span>',
  },

  es: {
    // Document chrome
    'doc.title': 'SPHEREx Vista del cielo · MoveScope',
    'doc.description': 'Globo 3D interactivo y selector de región celeste basado en datos reales de las cabeceras de exposición de SPHEREx.',
    'chip.count': '{frames} fotogramas / {exposures} exposiciones',

    // Brand / globe labels
    'brand.skyView': 'Vista del cielo',
    'brand.kicker': 'VISOR DE REGIÓN CELESTE',
    'globe.aria': 'Globo 3D',
    'label.site': 'Sitio cenital',
    'label.sky': 'Centro de región',
    'cursor.label': 'Cursor',
    'hemi.e': 'E',
    'hemi.w': 'O',

    // Header clock
    'now': 'Ahora',
    'now.title': 'Volver al instante real actual',
    'clock.mode.data': 'Época de datos',
    'clock.mode.now': 'En vivo',

    // Left panel
    'panel.left.title': 'Selector de región celeste',
    'panel.left.sub': 'REGIÓN CELESTE',
    'skymap.aria': 'Mapa de región de todo el cielo',
    'skymap.minimap.title': 'Miniatura de todo el cielo: clic para saltar',
    'skymap.tag': 'Todo el cielo · arrastrar = desplazar · rueda = zoom',
    'field.ra': 'RA',
    'field.dec': 'DEC',
    'field.fov': 'FOV',
    'unit.deg': 'grados',
    'toggle.track': 'Traza terrestre',
    'toggle.cov': 'Puntos terrestres de exposición',
    'toggle.mapcov': 'Cobertura celeste',
    'hint.main':
      'Clic en el globo para elegir la <b>región cenital</b>; ' +
      'arrastra el recuadro para moverla, rueda para ampliar, doble clic para restablecer.<br/>' +
      'Espacio: reproducir/pausar · ←/→: tiempo · N: ahora<br/>' +
      'Enlace directo: <span class="mono">?ra=269.452077&amp;dec=4.693365&amp;fov=0.5&amp;target=barnard</span>',

    // Right panel
    'panel.right.title': 'Región seleccionada',
    'panel.right.sub': 'SELECCIÓN',
    'kv.fov': 'Campo de visión',
    'kv.galactic': 'Galáctico',
    'kv.site': 'Sitio cenital',
    'kv.lst': 'Tiempo sidéreo local',
    'hits.head': 'Coincidencias locales <em>2.47°</em>',
    'hits.count': '{frames} fotogramas · {exposures} exposiciones · {nights} noches',
    'hit.frames': '{n} fotogramas',
    'hit.title': '{utc} · {n} fotogramas',
    'hit.toast': 'Ir a {obsid}',
    'curated': '<b>Épocas curadas {n}</b> · epoch_catalog → {date}',
    'hits.empty':
      'Sin coincidencias locales dentro de {radius}°.<br/>' +
      'Esto no significa que nunca se haya observado: el índice local es solo un subconjunto de {weeks} semanas de observación ' +
      '({frames} fotogramas / {exposures} exposiciones). Prueba otra franja de cobertura o haz clic en «Ahora» para ver otra época.',
    'copy.json': 'Copiar JSON',
    'copy.link': 'Copiar enlace',
    'open.movescope': 'Abrir en MoveScope',
    'movescope.target':
      'Se abrirá la región <b>{name}</b>; MoveScope analiza por coordenadas. Puedes cambiar de modo al entrar.',
    'movescope.custom':
      'Región personalizada; MoveScope analiza por coordenadas (REAL si existen épocas reales; si no, SIM). ' +
      'Puedes cambiar de modo al entrar.',
    'analysis.hint':
      'Un traspaso: en MoveScope cambia entre <b>Normal / Cascada-residual / Trayectoria</b>; ' +
      't0–t1 = filtro de análisis, no la época de datos.',
    'analysis.fovWarn':
      '<br><span style="color:var(--amber)">El FOV actual de {fov}° supera el límite de 3.0° del análisis de MoveScope; ' +
      'reduce el FOV primero.</span>',
    'data.note':
      'Datos: <span class="mono">data/fields.json</span> (tabla ' +
      '<span class="mono">frames</span> vía <span class="mono">tools/build_field_index.py</span>) · ' +
      '{frames} fotogramas / {exposures} exposiciones.',

    // Time bar
    'time.axis': 'Línea de tiempo UTC',
    'time.utc.placeholder': 'YYYY-MM-DD HH:MM',
    'time.utc.aria': 'Hora UTC (YYYY-MM-DD HH:MM)',
    'time.go': 'Ir',
    'time.playnote': 'Reproducción = tiempo continuo',
    'time.slider.aria': 'Posición temporal',
    'window.title': 'Ventana de tiempo de análisis',
    'window.days': 'días',
    'window.days.aria': 'Días de la ventana de tiempo de análisis',
    'window.custom': 'Personalizado: {days} días',
    'window.note': 'Ventana = <b>rango de filtrado de la capa de análisis</b>; no es la época de datos ni el instante actual.',
    'speed': 'Velocidad',
    'tracknote.default': 'Ventana de traza ±12 h',
    'tracknote.none': 'Sin exposiciones en ±12 h de esta época',
    'play.aria': 'Reproducir',
    'pause.aria': 'Pausar',
    'loader': 'Cargando el globo y el índice de exposiciones…',

    // Copy / handoff / errors
    'copy.ok': '{label} copiado',
    'copy.fail': 'No se pudo copiar {label}; cópialo manualmente.',
    'copy.label.json': 'JSON de la región',
    'copy.label.link': 'Enlace',
    'copy.label.movescope': 'Enlace de MoveScope',
    'movescope.blocked.copied': 'El navegador bloqueó la pestaña nueva; el enlace de MoveScope se ha copiado.',
    'movescope.blocked.manual': 'El navegador bloqueó la pestaña nueva; abre MoveScope manualmente.',
    'movescope.unreachable.copied':
      'No se puede conectar con {origin}: inicia MoveScope (app/server.py) primero. El enlace se ha copiado.',
    'movescope.unreachable': 'No se puede conectar con {origin}: inicia MoveScope (app/server.py) primero.',
    'utc.invalid': 'La hora UTC debe usar el formato YYYY-MM-DD HH:MM (UTC).',
    'utc.clamped': 'La hora UTC está fuera del índice local; se movió a {utc} UTC.',
    'step.edge': 'Se alcanzó el extremo del eje de tiempo.',
    'now.noTrack': 'El índice local termina el {date}; no hay traza terrestre para el instante actual.',
    'globe.pick': 'Región cenital → RA {ra}  DEC {dec}',
    'globe.site.value': 'Cenit {dec} {hemi}{lon}°',

    // Fatal loader messages
    'fatal.webgl':
      'Este navegador no admite <b>WebGL2</b> (o está desactivado), así que el globo 3D no puede iniciarse.<br/><br/>' +
      '<span style="color:#7d90a6">Es un ajuste de gráficos o del navegador, no un problema de red. ' +
      'Abre esta página en una versión reciente de Chrome / Safari, o consulta el estado de WebGL2 en chrome://gpu.</span>',
    'fatal.load':
      'Error al cargar: {msg}<br/><br/>' +
      'Esta página usa <span style="color:#d7e8f8">fetch</span> y módulos ES, así que debe servirse por HTTP local:<br/>' +
      '<span style="color:#d7e8f8">{cmd}</span> (puerto 8830 por defecto)<br/>' +
      'Luego abre <span style="color:#d7e8f8">{url}</span>',
  },
};

// Display names shown in presets, globe labels and the handoff hint.
// Coordinates and truth data still come from data/targets.json; this only
// translates the human-readable label (including the two targets whose source
// names use localized Chinese labels).
export const TARGET_NAMES = {
  en: {
    barnard: "Barnard's Star",
    epsind_b: 'Epsilon Indi B',
    wise0855: 'WISE 0855−0714',
    rw_aur: 'RW Aurigae',
    r_dra: 'R Draconis',
    m101: 'M101',
    campo_a: 'Field A (DR1)',
  },
  es: {
    barnard: 'Estrella de Barnard',
    epsind_b: 'Epsilon Indi B',
    wise0855: 'WISE 0855−0714',
    rw_aur: 'RW Aurigae',
    r_dra: 'R Draconis',
    m101: 'M101',
    campo_a: 'Campo A (DR1)',
  },
};

let lang = DEFAULT_LANG;

function normalizeLang(value) {
  const v = String(value ?? '').trim().toLowerCase();
  return LANGS.includes(v) ? v : null;
}

function queryLang() {
  if (typeof location === 'undefined') return null;
  const search = new URLSearchParams(location.search);
  const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
  return normalizeLang(search.get('lang') ?? hash.get('lang'));
}

function storedLang() {
  try {
    return normalizeLang(localStorage.getItem(LANG_KEY));
  } catch {
    return null;
  }
}

function saveLang(value) {
  try {
    localStorage.setItem(LANG_KEY, value);
  } catch {
    // localStorage can be unavailable (private mode / restricted contexts).
  }
}

function interpolate(template, vars) {
  return String(template).replace(/\{(\w+)\}/g, (whole, key) => {
    if (!vars || !Object.prototype.hasOwnProperty.call(vars, key)) return whole;
    return String(vars[key]);
  });
}

/** Translate a key for the current language. `{name}` placeholders are filled from vars. */
export function tr(key, vars = {}) {
  const table = DICTS[lang] || DICTS[DEFAULT_LANG];
  const template = table[key] ?? DICTS[DEFAULT_LANG][key];
  if (template === undefined) {
    if (typeof console !== 'undefined') console.warn(`[i18n] missing key: ${key} (${lang})`);
    return key;
  }
  return interpolate(template, vars);
}

export function getLang() {
  return lang;
}

function applyStatic() {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = tr(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-html]').forEach((el) => {
    el.innerHTML = tr(el.getAttribute('data-i18n-html'));
  });
  document.querySelectorAll('[data-i18n-aria-label]').forEach((el) => {
    el.setAttribute('aria-label', tr(el.getAttribute('data-i18n-aria-label')));
  });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => {
    el.setAttribute('title', tr(el.getAttribute('data-i18n-title')));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.setAttribute('placeholder', tr(el.getAttribute('data-i18n-placeholder')));
  });
  document.querySelectorAll('[data-i18n-content]').forEach((el) => {
    el.setAttribute('content', tr(el.getAttribute('data-i18n-content')));
  });
}

function updateSwitcher() {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('.lang-btn[data-lang]').forEach((btn) => {
    const active = btn.dataset.lang === lang;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

/** Read ?lang / hash / localStorage (in that order), apply the UI and return the active language. */
export function initI18n() {
  lang = queryLang() || storedLang() || DEFAULT_LANG;
  if (typeof document !== 'undefined') {
    document.documentElement.lang = lang;
    applyStatic();
    updateSwitcher();
  }
  saveLang(lang);
  return lang;
}

/** Switch language, re-apply static strings and remember the choice. Returns false for unknown values. */
export function setLang(next, { persist = true } = {}) {
  const normalized = normalizeLang(next);
  if (!normalized) return false;
  if (normalized === lang) return true;
  lang = normalized;
  if (typeof document !== 'undefined') {
    document.documentElement.lang = lang;
    applyStatic();
    updateSwitcher();
  }
  if (persist) saveLang(lang);
  return true;
}

/** Localized display name for a target object from data/targets.json (or a target id). */
export function targetLabel(target) {
  const id = typeof target === 'string' ? target : target?.id;
  if (id && TARGET_NAMES[lang]?.[id]) return TARGET_NAMES[lang][id];
  if (target && typeof target === 'object' && target.name) return String(target.name);
  return id ? String(id) : '';
}
