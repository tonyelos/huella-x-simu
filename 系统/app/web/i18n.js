/* MoveScope UI localisation (EN primary / ES secondary)
 *
 * - Default language is English; `?lang=en|es` overrides localStorage.
 * - Static text uses data-i18n / data-i18n-html / data-i18n-title /
 *   data-i18n-placeholder / data-i18n-aria-label.
 * - Dynamic client text uses I18n.t("key", params).
 * - Scientific prose returned by the API in Chinese is localised here:
 *   resultView() rebuilds labels/evidence/falsifier/caveats from structured
 *   fields where possible, and serverText()/modeNote() localise the remaining
 *   fixed/coded messages. No API or response fields are changed.
 *
 * Candidate/not-a-discovery semantics are deliberately preserved: the
 * dictionaries never assert a discovery or a confirmation.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.I18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SUPPORTED_LANGS = ["en", "es"];
  const DEFAULT_LANG = "en";
  const STORAGE_KEY = "movescope.lang";

  /* Each entry is [English, Spanish]; building both dictionaries from one
     table keeps the two languages structurally complete. */
  const STRINGS = {
    /* ---------- pages / shared header ---------- */
    /* ---------- badges / summary line ---------- */
    "badge.readiness": ["{level} readiness", "{level} preparación"],
    "badge.mode.real": ["REAL · Real", "REAL · Real"],
    "badge.mode.hybrid": ["HYBRID · Hybrid", "HYBRID · Híbrido"],
    "badge.mode.synthetic": ["SIM · Simulated", "SIM · Simulado"],
    "badge.mode.sample": ["SAMPLE", "MUESTRA"],
    "mode.short.real": ["REAL", "REAL"],
    "mode.short.hybrid": ["HYBRID", "HÍBRIDO"],
    "mode.short.synthetic": ["SIM", "SIM"],
    "mode.short.sample": ["SAMPLE", "MUESTRA"],
    "summary.epochs": ["{n} epochs", "{n} épocas"],
    "summary.fetch": ["fetched {succeeded}/{attempted} epochs · ", "obtenidas {succeeded}/{attempted} épocas · "],
    "summary.fit": ["fit {detected}/{n} epochs", "ajuste {detected}/{n} épocas"],
    "summary.sampleFit": ["parse/render check only", "solo verificación de lectura/renderizado"],
    "summary.baseline": [" · detection baseline {days} days", " · línea base de detección {days} días"],
    "time.windowLabel": ["TIME WINDOW", "VENTANA TEMPORAL"],

    /* ---------- canvas / small metadata ---------- */
    "canvas.targetLabel": ["TARGET", "OBJETIVO"],
    "canvas.zoom": ["Track zoom · {factor}×", "Zoom de trayectoria · {factor}×"],
    "draw.cascadeCounts": ["L1 {l1} · masked {masked} · residual {residual}", "L1 {l1} · enmascaradas {masked} · residuales {residual}"],
    "meta.dateMissing": ["date missing", "fecha ausente"],
    "meta.epochN": ["epoch {n}", "época {n}"],
    "meta.noMatchStats": ["No reference-star calibration data (single epoch or sample mode).", "Sin datos de calibración de estrellas de referencia (época única o modo muestra)."],
    "meta.matchTitle": ["Reference-star relative calibration", "Calibración relativa con estrellas de referencia"],
    "meta.matchChip": ["epoch {epoch} · {stars} stars · RMS {rms}", "época {epoch} · {stars} estrellas · RMS {rms}"],

    /* ---------- tooltips ---------- */
    "tip.lockReanalyze": ["Click to lock and re-analyze at these coordinates", "Pulsa para fijar y reanalizar en estas coordenadas"],
    "tip.notSelectable": ["not selectable", "no seleccionable"],
    "tip.trackNode": ["Click a node to jump frames and call /api/identify", "Pulsa un nodo para saltar de fotograma y llamar a /api/identify"],
    "tip.lockLayer": ["Click to lock and call /api/identify", "Pulsa para fijar y llamar a /api/identify"],

    /* ---------- lock card ---------- */
    "lock.identifying": ["Identifying", "Identificando"],
    "lock.queryingCatalog": ["Querying the Gaia DR3 catalog…", "Consultando el catálogo Gaia DR3…"],
    "lock.head": ["Locked source", "Fuente fijada"],
    "lock.unlock": ["Unlock", "Liberar"],
    "lock.origin.c": ["C ingested · {id}", "C ingerido · {id}"],
    "lock.origin.track": ["Track {id} · frame {n}", "Trayectoria {id} · fotograma {n}"],
    "lock.origin.cascade": ["{layer} · frame {n}", "{layer} · fotograma {n}"],
    "lock.origin.frame": ["Frame {n}", "Fotograma {n}"],
    "lock.hint.layer": ["Catalog identity only; no re-analysis is triggered. Residual/track candidates are not new objects.", "Solo identidad de catálogo; no se relanza el análisis. Los candidatos residuales/de trayectoria no son objetos nuevos."],
    "lock.hint.source": ["Coordinates are filled in above and used for analysis; C sources are compared with the catalog separately via /api/identify.", "Las coordenadas se rellenan arriba y se usan para el análisis; las fuentes C se comparan por separado con el catálogo mediante /api/identify."],

    /* ---------- misc notes ---------- */
    "note.serverError": ["Server returned an error", "El servidor devolvió un error"],
    "note.identifyApiError": ["identify API returned an error", "la API de identify devolvió un error"],
    "note.identifyCaveat": ["Catalog identification request failed: {msg}", "Falló la solicitud de identificación de catálogo: {msg}"],
    "note.lockCStatus": ["Locked C source RA <b>{ra}°</b> · Dec <b>{dec}°</b> · {label} ({status})", "Fuente C fijada AR <b>{ra}°</b> · Dec <b>{dec}°</b> · {label} ({status})"],

    /* ---------- table headers ---------- */
    "table.epoch": ["epoch", "época"],
    "table.sn": ["S/N", "S/N"],
    "table.radec": ["RA / Dec", "AR / Dec"],
    "table.ra": ["RA", "AR"],
    "table.dec": ["Dec", "Dec"],
    "table.catalog": ["catalog", "catálogo"],
    "table.origin": ["origin", "origen"],
    "table.epochTrack": ["epoch/track", "época/trayectoria"],
    "table.statusLevel": ["status/level", "estado/nivel"],
    "table.sep": ["sep", "sep"],
    "table.caveats": ["caveats", "advertencias"],
    "table.quality": ["quality", "calidad"],
    "table.stage": ["stage", "etapa"],

    "page.home.title": [
      "MoveScope · Sky Motion Analyzer — SPHEREx motion-pattern identification",
      "MoveScope · Analizador de movimiento celeste — identificación de patrones con SPHEREx",
    ],
    "page.cascade.title": [
      "MoveScope · Cascade / residual detection",
      "MoveScope · Detección en cascada / residuales",
    ],
    "page.tracks.title": [
      "MoveScope · Cross-epoch track candidates",
      "MoveScope · Candidatos de trayectoria entre épocas",
    ],
    "lang.label": ["Language", "Idioma"],
    "brand.tagline": ["Sky Motion", "Movimiento Celestial"],
    "brand.subtitle": [
      "Read motion patterns from multi-epoch SPHEREx images",
      "Lee patrones de movimiento en imágenes SPHEREx de varias épocas",
    ],
    "header.modeBadge.initial": ["Not analyzed", "Sin analizar"],
    "header.timeBadge.none": ["No time window", "Sin ventana temporal"],
    "header.expert.title": [
      "Show scientific details such as covariance, BIC and quality flags",
      "Mostrar detalles científicos como covarianza, BIC y marcas de calidad",
    ],
    "header.expert.label": ["Researcher mode", "Modo investigador"],
    "header.link.cascade": ["Cascade / residuals", "Cascada / residuales"],
    "header.link.tracks": ["Tracks", "Trayectorias"],
    "header.theory": ["Theory & design", "Teoría y diseño"],
    "header.cascade.badge": ["CASCADE MVP", "CASCADA MVP"],
    "header.cascade.linkView": ["Back to view", "Volver a la vista"],
    "header.cascade.linkTracks": ["Tracks layer", "Capa de trayectorias"],
    "header.cascade.back": ["← Back to main app", "← Volver a la app principal"],
    "header.tracks.badge": ["TRACKS MVP", "TRAYECTORIAS MVP"],
    "header.tracks.linkCascade": ["Cascade / residuals", "Cascada / residuales"],
    "header.tracks.back": ["← Main app", "← App principal"],

    /* ---------- home: input panel ---------- */
    "section.sky": ["Select sky field", "Seleccionar campo celeste"],
    "field.ra": ["RA", "AR"],
    "field.dec": ["Dec", "Dec"],
    "field.fov": ["FOV", "FOV"],
    "field.t0": ["Window t0", "Ventana t0"],
    "field.t1": ["Window t1", "Ventana t1"],
    "unit.deg": ["deg", "grados"],
    "unit.mjd": ["MJD", "MJD"],
    "field.fov.title": ["0.02–3.0 degrees", "0.02–3.0 grados"],
    "field.optional": ["optional", "opcional"],
    "field.mode": ["Data mode", "Modo de datos"],
    "mode.auto": ["Auto (real first)", "Automático (real primero)"],
    "mode.real": ["Real epochs only", "Solo épocas reales"],
    "mode.synthetic": ["Synthetic time series", "Serie temporal sintética"],
    "mode.sample": ["Offline sample (parse check)", "Muestra offline (verificación de lectura)"],
    "btn.identify": ["Identify motion pattern", "Identificar patrón de movimiento"],
    "note.start": [
      "Choose a target or enter coordinates to begin.",
      "Elige un objetivo o introduce coordenadas para empezar.",
    ],
    "preset.custom": ["Custom sky field", "Campo celeste personalizado"],
    "preset.customSub": ["RA / Dec / FOV", "AR / Dec / FOV"],
    "preset.official": ["Official", "Oficial"],
    "note.presetSelected": [
      "Selected <b>{name}</b>. Type {type}; expected pattern {expected}.",
      "Seleccionado <b>{name}</b>. Tipo {type}; patrón esperado {expected}.",
    ],
    "note.customSelected": [
      "Enter any coordinates, then click “Identify motion pattern”.",
      "Introduce cualquier coordenada y pulsa «Identificar patrón de movimiento».",
    ],
    "target.m101": ["M101 field", "Campo M101"],
    "target.campo_a": ["Field A (DR1)", "Campo A (DR1)"],

    /* ---------- A-source table ---------- */
    "aSource.summary": ["A detections (this analysis)", "Detecciones A (este análisis)"],
    "aSource.countSuffix": ["sources", "fuentes"],
    "aSource.batch": ["Batch identify A sources", "Identificar fuentes A en lote"],
    "aSource.batchBusy": ["Identifying…", "Identificando…"],
    "aSource.exportCsv": ["Export CSV", "Exportar CSV"],
    "aSource.exportJson": ["Export JSON", "Exportar JSON"],
    "aSource.directory": ["Unified candidate directory", "Catálogo unificado de candidatos"],
    "aSource.note": [
      "Click a row to lock that A source; batch badges are catalog identities, not discovery claims. no_match = uncatalogued candidate.",
      "Pulsa una fila para fijar esa fuente A; las etiquetas del lote son identidades de catálogo, no declaraciones de descubrimiento. no_match = candidato no catalogado.",
    ],
    "aSource.loadingBatch": [
      "Batch-identifying {n} A sources ({batches} batches)…",
      "Identificando {n} fuentes A en lote ({batches} lotes)…",
    ],
    "aSource.batchProgress": [
      "Batch-identifying A sources · batch {i}/{n}…",
      "Identificando fuentes A en lote · lote {i}/{n}…",
    ],
    "aSource.batchDone": [
      "Batch identification complete (gaia+2mass+wise): {summary}",
      "Identificación en lote completada (gaia+2mass+wise): {summary}",
    ],
    "aSource.batchFail": [
      "Batch identification failed: {msg}. The A-source table and existing badges remain unchanged.",
      "Falló la identificación en lote: {msg}. La tabla de fuentes A y las etiquetas existentes no cambian.",
    ],
    "aSource.summaryCounts": [
      "known {known} / ambiguous {ambiguous} / no_match {no_match} / insufficient {insufficient} / unavailable {unavailable}",
      "conocidas {known} / ambiguas {ambiguous} / no_match {no_match} / insuficientes {insufficient} / no_disponible {unavailable}",
    ],
    "aSource.summaryShown": [
      "Showing the first 50 of {n}; batch counts cover all sources.",
      "Mostrando las primeras 50 de {n}; los recuentos del lote cubren todas las fuentes.",
    ],
    "aSource.summaryAll": [
      "{n} sources; batch catalog identification is available.",
      "{n} fuentes; la identificación por catálogo en lote está disponible.",
    ],
    "aSource.badgeNoMatchTitle": [
      "Uncatalogued candidate, not an unknown object",
      "Candidato no catalogado, no un objeto desconocido",
    ],
    "aSource.qualityTitle": ["Source quality", "Calidad de la fuente"],
    "aSource.noMatchCaption": [
      "no_match = uncatalogued candidate",
      "no_match = candidato no catalogado",
    ],

    /* ---------- C any-field card ---------- */
    "anyfield.title": ["C any-field · P0", "C cualquier campo · P0"],
    "anyfield.load": ["Load C source list", "Cargar lista de fuentes C"],
    "anyfield.loading": ["Loading C source list…", "Cargando lista de fuentes C…"],
    "anyfield.batch": ["Batch identify C sources", "Identificar fuentes C en lote"],
    "anyfield.batchBusy": ["Batch identifying…", "Identificando en lote…"],
    "anyfield.batchLoading": [
      "Batch-identifying {n} C sources…",
      "Identificando {n} fuentes C en lote…",
    ],
    "anyfield.batchMissing": [
      "C sources are missing ra/dec/mjd; cannot batch-identify. The list is unchanged.",
      "Faltan ra/dec/mjd en las fuentes C; no se puede identificar en lote. La lista no cambia.",
    ],
    "anyfield.batchDone": [
      "<b>{summary}</b> · gaia+2mass+wise · catalog {status} · clicking a row still runs single-source identification",
      "<b>{summary}</b> · gaia+2mass+wise · catálogo {status} · pulsar una fila sigue ejecutando la identificación individual",
    ],
    "anyfield.batchFail": [
      "Batch identification failed: {msg}. The C-source list and existing results remain unchanged.",
      "Falló la identificación en lote: {msg}. La lista de fuentes C y los resultados existentes no cambian.",
    ],
    "anyfield.querying": ["Querying C coverage…", "Consultando la cobertura de C…"],
    "anyfield.optional": [
      "Optional enhancement: existing local data is used if C is unavailable.",
      "Mejora opcional: si C no está disponible se usan los datos locales existentes.",
    ],
    "anyfield.status.ok": ["C coverage available", "Cobertura C disponible"],
    "anyfield.status.no_coverage": ["C confirms no coverage", "C confirma que no hay cobertura"],
    "anyfield.status.index_incomplete": ["C index incomplete", "Índice de C incompleto"],
    "anyfield.status.unavailable": ["C service unavailable", "Servicio C no disponible"],
    "anyfield.status.unknown": ["C status unknown", "Estado de C desconocido"],
    "anyfield.body.ok": [
      "C local coverage: <b>{n}</b> epochs · footprint {method}{complete}",
      "Cobertura local de C: <b>{n}</b> épocas · huella {method}{complete}",
    ],
    "anyfield.body.no_coverage": [
      "C confirms no coverage within the complete region; existing local data and analysis conclusions are unchanged.",
      "C confirma que no hay cobertura en la región completa; los datos locales y las conclusiones del análisis no cambian.",
    ],
    "anyfield.body.index_incomplete": [
      "C index is incomplete; existing local data is used. A C gap does not mean “confirmed no coverage”.",
      "El índice de C está incompleto; se usan los datos locales existentes. Un hueco de C no significa «cobertura confirmada como ausente».",
    ],
    "anyfield.body.unavailable": [
      "C service is unavailable; existing local data is used.",
      "El servicio C no está disponible; se usan los datos locales existentes.",
    ],
    "anyfield.empty": [
      "C source list is empty: this field has not been ingested, or sources lie outside the search radius. An empty list does not mean “no objects”.",
      "La lista de fuentes C está vacía: este campo no se ha ingerido o las fuentes quedan fuera del radio de búsqueda. Una lista vacía no significa «sin objetos».",
    ],
    "anyfield.head": [
      "C source list: {n} sources (showing the first {shown}{truncated}){summary}",
      "Lista de fuentes C: {n} fuentes (mostrando las primeras {shown}{truncated}){summary}",
    ],
    "anyfield.truncated": [", truncated", ", truncada"],
    "anyfield.rowNote": [
      "Click a source → lock it and call /api/identify. Batch badges are catalog identities, not discovery claims.",
      "Pulsa una fuente → fíjala y llama a /api/identify. Las etiquetas del lote son identidades de catálogo, no declaraciones de descubrimiento.",
    ],
    "anyfield.noCoverageLoad": [
      "C confirms no coverage; there is no source list to load.",
      "C confirma que no hay cobertura; no hay lista de fuentes que cargar.",
    ],
    "anyfield.unavailableLoad": [
      "C source list unavailable (index incomplete); prior analysis and the local source list are unaffected.",
      "Lista de fuentes C no disponible (índice incompleto); el análisis previo y la lista local no se ven afectados.",
    ],
    "anyfield.unavailableGeneric": [
      "C source list unavailable; prior analysis and the local source list are unaffected.",
      "Lista de fuentes C no disponible; el análisis previo y la lista local no se ven afectados.",
    ],
    "anyfield.ingesting": [
      "Reading C ingested sources…",
      "Leyendo las fuentes ingeridas por C…",
    ],

    /* ---------- candidate directory ---------- */
    "dir.summary": ["Unified candidate directory", "Catálogo unificado de candidatos"],
    "dir.rows": ["rows", "filas"],
    "dir.note": [
      "Candidate directory is not a discovery catalogue; track rows are not new sources, and track nodes are not counted as separate sources.",
      "El catálogo de candidatos no es un catálogo de descubrimientos; las filas de trayectoria no son fuentes nuevas y los nodos de trayectoria no cuentan como fuentes separadas.",
    ],
    "dir.counts": [
      "rows {rows} · unique_sources {unique} · tracks {tracks} · known {known} · ambiguous {ambiguous} · no_match {no_match} · insufficient {insufficient} · unknown {unknown} · conflict {conflict}",
      "filas {rows} · unique_sources {unique} · trayectorias {tracks} · conocidas {known} · ambiguas {ambiguous} · no_match {no_match} · insuficientes {insufficient} · desconocidas {unknown} · conflicto {conflict}",
    ],
    "dir.loadingTracks": ["Reading tracklets…", "Leyendo tracklets…"],
    "dir.noContext": ["No analysis context available", "No hay contexto de análisis disponible"],
    "dir.tracksFail": [
      "Cannot read tracks: {msg}. Candidate directory not expanded.",
      "No se pueden leer las trayectorias: {msg}. El catálogo de candidatos no se expandió.",
    ],
    "dir.renderFail": [
      "Candidate directory rendering failed: {msg}. Source tables/badges are unchanged.",
      "Falló el renderizado del catálogo de candidatos: {msg}. Las tablas y etiquetas no cambian.",
    ],
    "dir.exportFail": [
      "Export failed: {msg}. The candidate table is unchanged.",
      "Falló la exportación: {msg}. La tabla de candidatos no cambia.",
    ],
    "dir.notReady": ["Unified candidate directory has not been generated yet", "El catálogo unificado de candidatos aún no se ha generado"],
    "dir.exportDone": [
      "Exported candidate directory {format}: {n} rows.",
      "Catálogo de candidatos exportado en {format}: {n} filas.",
    ],
    "dir.noRows": ["No candidate rows", "No hay filas de candidatos"],

    /* ---------- readiness / observation configuration ---------- */
    "section.readiness": ["Data readiness", "Preparación de los datos"],
    "ready.L0": ["1 epoch · can only tell whether a source exists", "1 época · solo indica si existe una fuente"],
    "ready.L1": ["2 epochs · one displacement only", "2 épocas · solo un desplazamiento"],
    "ready.L2": ["3–5 epochs · rate and direction", "3–5 épocas · velocidad y dirección"],
    "ready.L3": ["≥6 epochs · acceleration/period assessable", "≥6 épocas · aceleración/periodo evaluables"],
    "section.obs": ["Observation configuration", "Configuración de observaciones"],

    /* ---------- centre viewer ---------- */
    "layer.aria": ["Analysis layer", "Capa de análisis"],
    "layer.browse": ["Browse", "Explorar"],
    "layer.cascade": ["Cascade / residuals", "Cascada / residuales"],
    "layer.tracks": ["Tracks", "Trayectorias"],
    "layer.detail.cascade": ["Detailed cascade table ↗", "Tabla detallada de cascada ↗"],
    "layer.detail.tracks": ["Detailed tracks table ↗", "Tabla detallada de trayectorias ↗"],
    "viewer.eyebrow": ["SKY PATCH", "CAMPO CELESTE"],
    "viewer.waiting": ["Waiting for analysis", "Esperando análisis"],
    "modes.aria": ["Display mode", "Modo de visualización"],
    "mode.blink": ["Blink", "Parpadeo"],
    "mode.stack": ["Stack", "Apilado"],
    "mode.diff": ["Difference", "Diferencia"],
    "mode.track": ["Track", "Trayectoria"],
    "canvas.emptyTitle": [
      "Enter a sky field and bring multi-epoch images to life",
      "Introduce un campo celeste y da vida a imágenes de varias épocas",
    ],
    "canvas.emptySub": ["blink · stack · difference · trajectory", "parpadeo · apilado · diferencia · trayectoria"],
    "canvas.loading": ["Reading epochs / measurements / fit…", "Leyendo épocas / mediciones / ajuste…"],
    "timeline.play": ["Play", "Reproducir"],
    "legend.source": ["Detection · clickable", "Detección · seleccionable"],
    "legend.locked": ["Locked", "Fijada"],
    "legend.target": ["Target measurement position", "Posición medida del objetivo"],
    "legend.trail": ["Track", "Trayectoria"],
    "stretch.label": ["asinh · 6–99.6%", "asinh · 6–99.6%"],
    "canvas.emptyWindowTitle": ["No epochs in the time window", "No hay épocas en la ventana temporal"],
    "canvas.emptyWindowSub": [
      "No measurements or fit were made; no track was fabricated. Widen t0–t1.",
      "No se hicieron mediciones ni ajustes; no se fabricó ninguna trayectoria. Amplía t0–t1.",
    ],
    "epochLabel.emptyWindow": ["No epochs in window", "Sin épocas en la ventana"],

    /* ---------- right analysis panel ---------- */
    "layer.title.default": ["Analysis layer", "Capa de análisis"],
    "layer.title.cascade": ["Cascade / residuals · current frame", "Cascada / residuales · fotograma actual"],
    "layer.title.tracks": ["Tracks · D0–D4", "Trayectorias · D0–D4"],
    "layer.disclaimer.default": [
      "Candidates are not new objects; no discovery claim.",
      "Los candidatos no son objetos nuevos; no se declara un descubrimiento.",
    ],
    "layer.disclaimer.cascade": [
      "Residual candidates are not new objects; unmatched does not mean a new star.",
      "Los candidatos residuales no son objetos nuevos; sin coincidencia no significa una estrella nueva.",
    ],
    "layer.disclaimer.tracks": [
      "Track candidates are not new objects; D5 requires manual/follow-up confirmation.",
      "Los candidatos de trayectoria no son objetos nuevos; D5 requiere confirmación manual o de seguimiento.",
    ],
    "layer.badge.loading": ["Loading", "Cargando"],
    "layer.badge.candidate": ["candidate", "candidato"],
    "layer.badge.unavailable": ["Unavailable", "No disponible"],
    "layer.tracks.tip": [
      "Track lines are colored by D0–D4; click a track/node for details.",
      "Las líneas de trayectoria se colorean por D0–D4; pulsa una trayectoria o nodo para ver detalles.",
    ],
    "layer.tracks.detailTip": [
      "Click a track line to select it; click a node to jump frames and call /api/identify.",
      "Pulsa una línea de trayectoria para seleccionarla; pulsa un nodo para saltar de fotograma y llamar a /api/identify.",
    ],
    "layer.tracks.loading": [
      "Reading analysis layer; /api/analyze is not re-run.",
      "Leyendo la capa de análisis; no se vuelve a ejecutar /api/analyze.",
    ],
    "layer.tracks.notLoaded": ["Track data has not been loaded yet.", "Los datos de trayectorias aún no se han cargado."],
    "layer.cascade.notLoaded": ["Cascade data has not been loaded yet.", "Los datos de cascada aún no se han cargado."],
    "layer.cascade.badgeFrame": [
      "Current frame {l1}/{masked}/{residual}",
      "Fotograma actual {l1}/{masked}/{residual}",
    ],
    "layer.cascade.badgeNone": ["No current frame", "Sin fotograma actual"],
    "layer.cascade.window": ["Window {text}", "Ventana {text}"],
    "layer.cascade.toggleL1": ["L1 filled", "L1 relleno"],
    "layer.cascade.toggleMasked": ["masked grey cross", "cruz gris enmascarada"],
    "layer.cascade.toggleResidual": ["residual open", "residual abierto"],
    "layer.cascade.tip": [
      "L1 filled · masked grey cross/circle · residual open amber; click a residual to lock it and call /api/identify.",
      "L1 relleno · cruz/círculo gris enmascarado · residual abierto ámbar; pulsa un residual para fijarlo y llamar a /api/identify.",
    ],
    "layer.cascade.detailDefault": [
      "L1 filled · masked grey cross/circle · residual open amber; click a residual to lock and call /api/identify.",
      "L1 relleno · cruz/círculo gris enmascarado · residual abierto ámbar; pulsa un residual para fijarlo y llamar a /api/identify.",
    ],
    "layer.cascade.selected": ["Selected {layer}", "Seleccionado {layer}"],
    "layer.cascade.queryingCatalog": ["Querying catalog…", "Consultando catálogo…"],
    "layer.kind.l1": ["L1 source", "Fuente L1"],
    "layer.kind.masked": ["masked source", "fuente enmascarada"],
    "layer.kind.residual": ["residual source", "fuente residual"],
    "layer.kind.track": ["track node", "nodo de trayectoria"],
    "layer.kind.node": ["layer node", "nodo de capa"],
    "layer.stats.total": ["total {n}", "total {n}"],
    "layer.stats.epochs": ["{n} epochs", "{n} épocas"],
    "layer.loadFail": ["{msg}. No layer data was fabricated.", "{msg}. No se fabricaron datos de capa."],
    "layer.error.noEpochs": [
      "No epochs in the time window; there is no analysis layer to overlay.",
      "No hay épocas en la ventana temporal; no hay capa de análisis que superponer.",
    ],
    "layer.error.cascadeFetch": ["cascade API returned an error", "la API de cascada devolvió un error"],
    "layer.error.tracksFetch": ["tracks API returned an error", "la API de trayectorias devolvió un error"],
    "layer.error.noData": ["No layer data was fabricated.", "No se fabricaron datos de capa."],

    /* ---------- verdict / metrics / charts ---------- */
    "verdict.eyebrow": ["MOTION FINGERPRINT", "HUELLA DE MOVIMIENTO"],
    "verdict.emptyTitle": ["Not analyzed yet", "Aún sin analizar"],
    "verdict.emptyText": [
      "Run an identification to see motion patterns and the evidence chain.",
      "Ejecuta una identificación para ver los patrones de movimiento y la cadena de evidencia.",
    ],
    "verdict.confidence": ["Confidence {pct}%", "Confianza {pct}%"],
    "verdict.catalogPriorBadge": [" · CATALOG PRIOR", " · PRIOR DE CATÁLOGO"],
    "verdict.catalogPriorLabel": ["L · Gaia prior", "L · prior de Gaia"],
    "verdict.catalogPriorText": [
      "SPHEREx did not measure this independently; using the Gaia PM prior; not a discovery",
      "SPHEREx no midió esto de forma independiente; se usa el prior de PM de Gaia; no es un descubrimiento",
    ],
    "verdict.moving": [" · significant displacement", " · desplazamiento significativo"],
    "verdict.variable": [" · significant variability", " · variabilidad significativa"],
    "verdict.sampleLabel": ["Sample check (no target measurement)", "Verificación de muestra (sin medición del objetivo)"],
    "verdict.sampleText": [
      "The two images come from different sky fields · parsing and rendering check only",
      "Las dos imágenes proceden de campos celestes distintos · solo se verifica lectura y renderizado",
    ],
    "verdict.emptyWindowLabel": ["No epochs in window", "Sin épocas en la ventana"],
    "verdict.emptyWindowText": [
      "Not enough images to assess motion or variability.",
      "No hay imágenes suficientes para evaluar movimiento o variabilidad.",
    ],
    "metric.mu": ["Proper motion μ", "Movimiento propio μ"],
    "metric.gaiaPriorMu": ["Gaia prior μ", "Prior de Gaia μ"],
    "metric.pa": ["Direction PA", "Dirección PA"],
    "metric.spherexMu": ["SPHEREx measured μ", "μ medida por SPHEREx"],
    "metric.displacement": ["First–last displacement", "Desplazamiento inicial–final"],
    "metric.amp": ["Brightness amplitude", "Amplitud de brillo"],
    "metric.period": ["Period candidate", "Periodo candidato"],
    "metric.accel": ["Acceleration significance", "Significancia de aceleración"],
    "metric.linearity": ["Track non-linearity", "No linealidad de la trayectoria"],
    "metric.epochs": ["Effective epochs", "Épocas efectivas"],
    "metric.epochUnit": ["epochs", "épocas"],
    "metric.gaiaPrior": ["Gaia prior", "Prior de Gaia"],
    "metric.threeSigma": ["3σ", "3σ"],
    "catalog.head": ["Catalog identification · target source", "Identificación en catálogo · fuente objetivo"],
    "chart.lightCurve": ["Light curve", "Curva de luz"],
    "chart.trackPlane": ["Tangent-plane track", "Trayectoria en el plano tangente"],
    "chart.models": ["Model selection · BIC", "Selección de modelos · BIC"],
    "chart.probability": ["Probability", "Probabilidad"],
    "chart.insufficientEpochs": [
      "Not enough epochs for model comparison.",
      "No hay épocas suficientes para comparar modelos.",
    ],
    "chart.multiBandNote1": [
      "Multi-band epochs: fluxes from different detectors are not directly comparable",
      "Épocas multibanda: los flujos de detectores distintos no son directamente comparables",
    ],
    "chart.multiBandNote2": [
      "No absolute flux calibration, so variability is not assessed",
      "Sin calibración absoluta de flujo, no se evalúa la variabilidad",
    ],
    "chart.multiBandMeta": ["Multi-band · variability not assessed", "Multibanda · variabilidad no evaluada"],
    "chart.relativeFlux": ["Relative flux", "Flujo relativo"],
    "chart.epochsBaseline": ["{n} epochs · baseline {med}", "{n} épocas · referencia {med}"],
    "chart.eastXi": ["East ξ (″)", "Este ξ (″)"],
    "chart.northEta": ["North η (″)", "Norte η (″)"],
    "evidence.title": ["Evidence chain", "Cadena de evidencia"],
    "evidence.falsifier": ["Falsifiability conditions", "Condiciones de falsabilidad"],
    "evidence.caveats": ["Caveats", "Advertencias"],
    "evidence.truth": ["Injected truth", "Verdad inyectada"],
    "evidence.recovered": ["Recovered", "Recuperado"],
    "action.download": ["Download JSON", "Descargar JSON"],
    "action.copyLink": ["Copy embeddable link", "Copiar enlace insertable"],
    "action.copied": ["Copied", "Copiado"],

    /* ---------- footer ---------- */
    "footer.data": [
      "Data: NASA/IPAC IRSA · SPHEREx level2 (QR3 / DR1)",
      "Datos: NASA/IPAC IRSA · SPHEREx level2 (QR3 / DR1)",
    ],
    "footer.method": [
      "Method: tangent-plane weighted least squares · BIC model selection · reference-star relative astrometry",
      "Método: mínimos cuadrados ponderados en el plano tangente · selección de modelos por BIC · astrometría relativa a estrellas de referencia",
    ],
    "footer.sim": [
      "Simulated data are always labelled <b>SIM/HYBRID</b>",
      "Los datos simulados siempre se etiquetan como <b>SIM/HYBRID</b>",
    ],

    /* ---------- theory modal ---------- */
    "theory.close": ["Close", "Cerrar"],
    "theory.eyebrow": ["THEORY · Design overview", "TEORÍA · Resumen del diseño"],
    "theory.title": ["One object, five motion patterns", "Un objeto, cinco patrones de movimiento"],
    "theory.lead": [
      "The tool does not guess from images. It fits the position series and the brightness series separately, selects one model family by BIC, and reports falsifiable conclusions.",
      "La herramienta no adivina a partir de las imágenes. Ajusta por separado la serie de posiciones y la de brillo, selecciona una familia de modelos por BIC y presenta conclusiones falsables.",
    ],
    "theory.grid1.title": ["① Measurement", "① Medición"],
    "theory.grid1.text": [
      "PSF matched-filter detection; PSF-template centroid; aperture photometry; three-stage FLAGS mask; affine calibration against reference stars.",
      "Detección por filtro adaptado a la PSF; centroide por plantilla PSF; fotometría de apertura; máscara FLAGS en tres etapas; calibración afín con estrellas de referencia.",
    ],
    "theory.grid2.title": ["② Model family", "② Familia de modelos"],
    "theory.grid2.text": [
      "M0 stationary · M1 variable · M2 uniform proper motion · M3 proper motion + parallax · M4 proper motion + acceleration. Joint χ² over position and flux.",
      "M0 estacionario · M1 variable · M2 movimiento propio uniforme · M3 movimiento propio + paralaje · M4 movimiento propio + aceleración. χ² conjunta de posición y flujo.",
    ],
    "theory.grid3.title": ["③ Criteria", "③ Criterios"],
    "theory.grid3.text": [
      "A conclusion requires ΔBIC &gt; 6 and parameters &gt; 3σ; data readiness L0–L3 sets the strength of the conclusion.",
      "Una conclusión requiere ΔBIC &gt; 6 y parámetros &gt; 3σ; la preparación de datos L0–L3 determina la fuerza de la conclusión.",
    ],
    "theory.grid4.title": ["④ Counterexample", "④ Contraejemplo"],
    "theory.grid4.text": [
      "A variable star stays put while its brightness changes. Position and flux channels are separate, so “brighter” is not mistaken for “moving”.",
      "Una estrella variable no se desplaza mientras cambia su brillo. Los canales de posición y flujo están separados, así que «más brillante» no se confunde con «en movimiento».",
    ],
    "theory.table.pattern": ["Pattern", "Patrón"],
    "theory.table.mechanism": ["Generating mechanism", "Mecanismo generador"],
    "theory.table.signature": ["Observable signature", "Firma observable"],
    "theory.table.minEpochs": ["Minimum epochs", "Épocas mínimas"],
    "theory.rowS": ["S stationary", "S estacionario"],
    "theory.rowS.mechanism": ["Background star / galaxy", "Estrella / galaxia de fondo"],
    "theory.rowS.signature": ["Slope consistent with 0", "Pendiente compatible con 0"],
    "theory.rowS.min": ["2 (upper limit)", "2 (límite superior)"],
    "theory.rowV": ["V variable", "V variable"],
    "theory.rowV.mechanism": ["Pulsation / outburst / eclipse / AGN", "Pulsación / estallido / eclipse / AGN"],
    "theory.rowV.signature": ["Flux changes significantly, position does not", "El flujo cambia de forma significativa; la posición no"],
    "theory.rowV.min": ["3", "3"],
    "theory.rowL": ["L uniform linear", "L lineal uniforme"],
    "theory.rowL.mechanism": ["Nearby-star proper motion", "Movimiento propio de una estrella cercana"],
    "theory.rowL.signature": ["Tangent-plane straight line, significant slope", "Línea recta en el plano tangente con pendiente significativa"],
    "theory.rowL.min": ["3", "3"],
    "theory.rowA": ["A accelerating / curved", "A acelerado / curvo"],
    "theory.rowA.mechanism": ["Solar System object / binary", "Objeto del sistema solar / binaria"],
    "theory.rowA.signature": ["Quadratic term significantly better than a line", "El término cuadrático mejora significativamente la recta"],
    "theory.rowA.min": ["4", "4"],
    "theory.rowP": ["P parallax", "P paralaje"],
    "theory.rowP.mechanism": ["Earth's orbit around the Sun", "Órbita de la Tierra alrededor del Sol"],
    "theory.rowP.signature": ["Residuals correlate with Earth's position vector", "Los residuales se correlacionan con el vector de posición terrestre"],
    "theory.rowP.min": ["≥6, spanning 1 year", "≥6, abarcando 1 año"],
    "theory.fine": [
      "Full documentation: internal theory-design notes (Chinese). Data access: static IRSA slices + S3 header probes (TAP/SIA unavailable during collection).",
      "Documentación completa: notas internas de diseño teórico (en chino). Acceso a datos: cortes estáticos de IRSA + sondeos de cabecera S3 (TAP/SIA no disponibles durante la recogida).",
    ],

    /* ---------- time window / analysis notes ---------- */
    "time.allPeriods": ["All periods", "Todos los periodos"],
    "time.allPeriodsRequested": ["All periods · {n} epochs", "Todos los periodos · {n} épocas"],
    "time.window": [
      "Time window MJD {t0}–{t1} · kept {kept}/{requested} epochs",
      "Ventana temporal MJD {t0}–{t1} · conservadas {kept}/{requested} épocas",
    ],
    "time.fromViewPrefix": ["From view · ", "Desde la vista · "],
    "time.insufficient": [" · insufficient", " · insuficiente"],
    "note.fovInvalid": [
      "Field of view (FOV) must be between <b>0.02–3.0 degrees</b> (current {value}).",
      "El campo de visión (FOV) debe estar entre <b>0.02–3.0 grados</b> (actual {value}).",
    ],
    "note.raDecInvalid": ["Enter valid RA / Dec in degrees.", "Introduce AR / Dec válidos en grados."],
    "note.readTimeBoth": ["t0 and t1 must both be filled in (MJD).", "t0 y t1 deben rellenarse ambos (MJD)."],
    "note.readTimeNumber": ["t0/t1 must be numeric MJD values.", "t0/t1 deben ser valores MJD numéricos."],
    "note.readTimeOrder": ["t0 must be ≤ t1.", "t0 debe ser ≤ t1."],
    "note.analyzeFail": ["Analysis failed: {msg}", "El análisis falló: {msg}"],
    "note.loadTargetsFail": ["Could not load the target list: {msg}", "No se pudo cargar la lista de objetivos: {msg}"],
    "note.layerApiError": ["{mode} API returned an error", "La API de {mode} devolvió un error"],
    "note.layerFail": ["{msg}. No layer data was fabricated.", "{msg}. No se fabricaron datos de capa."],
    "note.lockC": [
      "Locked C source {id}: RA {ra}° · Dec {dec}°; catalog identification requested…",
      "Fuente C fijada {id}: AR {ra}° · Dec {dec}°; identificación de catálogo solicitada…",
    ],
    "note.lockSource": [
      "Locked source: RA {ra}° · Dec {dec}° · S/N {snr}; re-analyzing at these coordinates…",
      "Fuente fijada: AR {ra}° · Dec {dec}° · S/N {snr}; reanalizando en estas coordenadas…",
    ],
    "note.lockLayerSource": [
      "Locked <b>{label}</b>: RA {ra}° · Dec {dec}° · S/N {snr}; querying catalog…",
      "Fijado <b>{label}</b>: AR {ra}° · Dec {dec}° · S/N {snr}; consultando catálogo…",
    ],
    "note.unlock": [
      "Source lock released; coordinate inputs are preserved for editing or re-analysis.",
      "Bloqueo de fuente liberado; las coordenadas introducidas se conservan para editar o reanalizar.",
    ],
    "note.lockLayerStatus": [
      "Locked <b>{layer}</b> · RA {ra}° · Dec {dec}° · {label} ({status})",
      "Fijado <b>{layer}</b> · AR {ra}° · Dec {dec}° · {label} ({status})",
    ],
    "note.identifyFail": ["Identification request failed: {msg}", "Falló la solicitud de identificación: {msg}"],
    "note.missingCoordsMjd": [
      "Coordinates/MJD are missing; the catalog cannot be queried.",
      "Faltan coordenadas/MJD; no se puede consultar el catálogo.",
    ],
    "note.identifyDisclaimer": [
      "Unmatched does not mean a new object; catalog unavailable does not mean “uncatalogued”.",
      "Sin coincidencia no significa un objeto nuevo; catálogo no disponible no significa «no catalogado».",
    ],
    "note.catalogUnavailable": ["Catalog unavailable", "Catálogo no disponible"],
    "note.exportDone": [
      "Exported {n} {kind} sources (CSV expands candidates; JSON keeps candidates/byId).",
      "Exportadas {n} fuentes {kind} (el CSV expande candidatos; el JSON conserva candidates/byId).",
    ],
    "note.exportCsvDone": [
      "Exported {n} CSV rows (with header); export does not change the table or badges.",
      "Exportadas {n} filas CSV (con cabecera); la exportación no cambia la tabla ni las etiquetas.",
    ],
    "note.exportFail": [
      "Export failed: {msg}. The table and badges are unchanged.",
      "Falló la exportación: {msg}. La tabla y las etiquetas no cambian.",
    ],
    "note.exportNotReadyA": ["A sources have not been batch-identified yet", "Las fuentes A aún no se han identificado en lote"],
    "note.exportNotReadyC": ["C sources have not been batch-identified yet", "Las fuentes C aún no se han identificado en lote"],
    "note.exportServerFail": ["Export failed", "Falló la exportación"],

    /* ---------- class labels (server semantics, local wording) ---------- */
    "class.L.label": ["Linear motion", "Movimiento lineal"],
    "class.L.sub": ["High-proper-motion star / brown-dwarf candidate", "Candidata a estrella de alto movimiento propio / enana marrón"],
    "class.V.label": ["Variable", "Variable"],
    "class.V.sub": ["Position fixed, brightness changing", "Posición fija, brillo cambiante"],
    "class.S.label": ["Stationary", "Estacionario"],
    "class.S.sub": ["No significant displacement or variability", "Sin desplazamiento ni variabilidad significativos"],
    "class.A.label": ["Accelerating / curved", "Acelerado / curvo"],
    "class.A.sub": ["Solar System body or orbital-motion candidate", "Candidato a objeto del sistema solar o movimiento orbital"],
    "class.P.label": ["Parallax", "Paralaje"],
    "class.P.sub": ["Annual apparent motion", "Movimiento aparente anual"],
    "class.O.label": ["Orbital motion", "Movimiento orbital"],
    "class.O.sub": ["Bound binary / companion", "Binaria ligada / compañera"],
    "class.U.label": ["Undetermined", "Indeterminado"],
    "class.U.sub": ["Insufficient data for a determination", "Datos insuficientes para determinarlo"],

    /* ---------- result labels / public text ---------- */
    "result.label.sample": ["Sample check (no target measurement)", "Verificación de muestra (sin medición del objetivo)"],
    "result.label.fetchFailed": ["Undetermined (fetch failed)", "Indeterminado (falló la obtención)"],
    "result.label.emptyWindow": ["No epochs in the time window", "No hay épocas en la ventana temporal"],
    "result.label.notDetected": ["Undetermined (target not detected)", "Indeterminado (objetivo no detectado)"],
    "result.label.associationSuspicious": ["Undetermined (suspicious association)", "Indeterminado (asociación sospechosa)"],
    "result.label.catalogPrior": ["L · Gaia prior (SPHEREx did not measure independently)", "L · prior de Gaia (SPHEREx no midió de forma independiente)"],
    "result.public.sample": [
      "This is “offline sample” mode: the two images come from M101 and Field A, different sky fields, so they cannot be used to judge whether any object moved. This mode only checks that files can be read and images can be drawn.",
      "Este es el modo «muestra offline»: las dos imágenes proceden de M101 y del Campo A, campos celestes distintos, por lo que no sirven para juzgar si algún objeto se movió. Este modo solo comprueba que los archivos se leen y las imágenes se dibujan.",
    ],
    "result.public.fetchFailed": [
      "No real images were retrieved, so it is not possible to determine whether the object moved. Check the network and try again later.",
      "No se obtuvieron imágenes reales, así que no es posible determinar si el objeto se movió. Comprueba la red e inténtalo más tarde.",
    ],
    "result.public.emptyWindow": [
      "The current time window has no usable epochs, so no motion or variability measurement was made.",
      "La ventana temporal actual no tiene épocas utilizables, así que no se hizo ninguna medición de movimiento o variabilidad.",
    ],
    "result.public.notDetected": [
      "In this sky field and these epochs, the target was not detected. It may be too faint, or the predicted position may be off. Deeper data or a coordinate check is needed.",
      "En este campo celeste y estas épocas no se detectó el objetivo. Puede ser demasiado débil o la posición prevista puede estar desviada. Se necesitan datos más profundos o verificar las coordenadas.",
    ],
    "result.public.associationSuspicious": [
      "The fitted proper motion of {mu} arcsec/yr is far beyond the physical limit for a star or brown dwarf; different objects from different epochs were probably linked into one track. This result is unreliable and needs manual review.",
      "El movimiento propio ajustado de {mu} arcsec/año supera ampliamente el límite físico de una estrella o enana marrón; probablemente se enlazaron objetos distintos de épocas distintas en una sola trayectoria. Este resultado no es fiable y requiere revisión manual.",
    ],
    "result.public.catalogPrior": [
      "SPHEREx did not independently measure this target's motion in this run; the Gaia PM prior is used to classify it as an L candidate. This is a catalog prior, not a SPHEREx measurement, a discovery, or a confirmation.",
      "SPHEREx no midió de forma independiente el movimiento de este objetivo en esta ejecución; se usa el prior de PM de Gaia para clasificarlo como candidato L. Es un prior de catálogo, no una medición de SPHEREx, un descubrimiento ni una confirmación.",
    ],
    "result.public.v": [
      "Across {n} observations the target's position is essentially fixed (change within the errors), but its brightness varies by about {amp} mag. It is a variable star, not a moving object.",
      "En {n} observaciones la posición del objetivo permanece esencialmente fija (cambio dentro de los errores), pero su brillo varía unas {amp} mag. Es una estrella variable, no un objeto en movimiento.",
    ],
    "result.public.l": [
      "Over {days} days the target moved {disp} arcsec on the sky in direction {pa}°. That is about {mu} arcsec per year — a uniform linear motion, typical of a high-proper-motion star or brown dwarf.",
      "En {days} días el objetivo se desplazó {disp} arcsec en el cielo en dirección {pa}°. Eso equivale a unos {mu} arcsec por año — un movimiento lineal uniforme, típico de una estrella de alto movimiento propio o una enana marrón.",
    ],
    "result.public.a": [
      "The target moved {disp} arcsec and its track is clearly curved (acceleration significance {accel}σ). This is more like a Solar System object or a binary orbit than a straight proper-motion track.",
      "El objetivo se desplazó {disp} arcsec y su trayectoria es claramente curva (significancia de aceleración {accel}σ). Se parece más a un objeto del sistema solar o a una órbita binaria que a una trayectoria recta de movimiento propio.",
    ],
    "result.public.sDetailed": [
      "Across {n} observations the displacement is {disp} ± {err} arcsec, consistent with 0 within 3σ; the current data cannot measure a reliable proper motion. It may be a background star or distant galaxy, or a very slow-moving object.",
      "En {n} observaciones el desplazamiento es {disp} ± {err} arcsec, compatible con 0 dentro de 3σ; los datos actuales no permiten medir un movimiento propio fiable. Puede ser una estrella de fondo, una galaxia lejana o un objeto que se mueve muy lentamente.",
    ],
    "result.public.s": [
      "Across {n} observations neither position nor brightness changed significantly. It may be a background star or distant galaxy; the current data cannot measure motion.",
      "En {n} observaciones ni la posición ni el brillo cambiaron de forma significativa. Puede ser una estrella de fondo o una galaxia lejana; los datos actuales no permiten medir movimiento.",
    ],
    "result.public.uOneEpoch": [
      "The target was reliably detected in only 1 epoch; the other epochs did not reach the detection threshold. This usually means the target is too faint or the coordinates are offset. Deeper data or a catalog coordinate check is needed.",
      "El objetivo se detectó de forma fiable en solo 1 época; las demás no alcanzaron el umbral de detección. Normalmente significa que es demasiado débil o que las coordenadas están desviadas. Se necesitan datos más profundos o verificar las coordenadas con el catálogo.",
    ],
    "result.public.u": [
      "The data are insufficient to determine this object's motion pattern. More epochs are needed (at least 3, preferably spanning 6 months).",
      "Los datos son insuficientes para determinar el patrón de movimiento de este objeto. Se necesitan más épocas (al menos 3, preferiblemente abarcando 6 meses).",
    ],

    /* ---------- evidence / falsifier ---------- */
    "result.evidence.displacement": [
      "Displacement {disp} ± {err} arcsec, direction {pa}° (north through east)",
      "Desplazamiento {disp} ± {err} arcsec, dirección {pa}° (de norte a este)",
    ],
    "result.evidence.mu": [
      "Fitted proper motion {mu} mas/yr (≈{px} pixels/yr)",
      "Movimiento propio ajustado {mu} mas/yr (≈{px} píxeles/año)",
    ],
    "result.evidence.consistentZero": [
      "Position change consistent with 0 (<5σ)",
      "Cambio de posición compatible con 0 (<5σ)",
    ],
    "result.evidence.amp": ["Brightness amplitude {amp} mag", "Amplitud de brillo {amp} mag"],
    "result.evidence.period": ["Period candidate {period} days", "Periodo candidato {period} días"],
    "result.evidence.epochs": [
      "Effective epochs {n}, baseline {days} days",
      "Épocas efectivas {n}, línea base {days} días",
    ],
    "result.evidence.fetchFailed": [
      "Real-epoch retrieval failed: {succeeded}/{attempted} epochs succeeded",
      "Falló la obtención de épocas reales: {succeeded}/{attempted} épocas correctas",
    ],
    "result.evidence.sample1": ["Two offline samples come from M101 and Field A, two different sky fields", "Las dos muestras offline proceden de M101 y del Campo A, dos campos celestes distintos"],
    "result.evidence.sample2": ["Samples are used only to check FITS parsing, HDU cutouts and rendering", "Las muestras solo verifican el análisis FITS, los recortes HDU y el renderizado"],
    "result.evidence.sample3": ["Sample mode makes no centroid/displacement/variability measurement for any target", "El modo muestra no mide centroide, desplazamiento ni variabilidad de ningún objetivo"],
    "result.evidence.emptyWindow": ["No usable epochs in the time window; no source table, model or motion conclusion was produced.", "No hay épocas utilizables en la ventana temporal; no se generó tabla de fuentes, modelo ni conclusión de movimiento."],
    "result.evidence.notDetected": ["No 3σ detection near the predicted position", "No hay detección a 3σ cerca de la posición prevista"],
    "result.evidence.attemptedEpochs": ["Attempted {n} epochs", "Se intentaron {n} épocas"],
    "result.evidence.outOfFrame": [
      "{n} epochs had the predicted target position outside the cutout ({ids}…)",
      "{n} épocas tenían la posición prevista del objetivo fuera del recorte ({ids}…)",
    ],
    "result.evidence.outOfFrameSkipped": [
      "{n} epochs had the predicted target position outside the cutout and were skipped: {ids}",
      "{n} épocas tenían la posición prevista del objetivo fuera del recorte y se omitieron: {ids}",
    ],
    "result.falsifier.v": [
      "If later epochs show position moving significantly at >5σ, the variable-star criterion fails and the object should be reclassified as moving.",
      "Si épocas posteriores muestran un movimiento de posición significativo a >5σ, el criterio de estrella variable falla y el objeto debe reclasificarse como móvil.",
    ],
    "result.falsifier.l": [
      "If new epochs show a significantly curved track (ΔBIC>10) or a displacement that is non-linear over a longer baseline, the “uniform linear motion” conclusion is overturned.",
      "Si nuevas épocas muestran una trayectoria significativamente curva (ΔBIC>10) o un desplazamiento no lineal en una línea base mayor, se refuta la conclusión de «movimiento lineal uniforme».",
    ],
    "result.falsifier.a": [
      "If more epochs show the quadratic term is not significant (<3σ), the classification falls back to uniform linear motion.",
      "Si más épocas muestran que el término cuadrático no es significativo (<3σ), la clasificación vuelve a movimiento lineal uniforme.",
    ],
    "result.falsifier.u": [
      "If added epochs make displacement or brightness change significant at >5σ, the “undetermined” result should be upgraded.",
      "Si al añadir épocas el desplazamiento o el cambio de brillo se vuelven significativos a >5σ, el resultado «indeterminado» debe actualizarse.",
    ],
    "result.falsifier.notDetected": [
      "If the target appears in deeper observations or a wider field, this conclusion no longer holds.",
      "Si el objetivo aparece en observaciones más profundas o en un campo más amplio, esta conclusión deja de ser válida.",
    ],
    "result.falsifier.emptyWindow": [
      "Retry with a time window that contains epochs.",
      "Vuelve a intentarlo con una ventana temporal que contenga épocas.",
    ],
    "result.falsifier.fetchFailed": [
      "If the network/IRSA recovers and the cutouts can be retrieved, re-run the analysis; the current result is not used to judge motion.",
      "Si la red/IRSA se recupera y pueden obtenerse los recortes, vuelve a ejecutar el análisis; el resultado actual no se usa para juzgar el movimiento.",
    ],
    "tracks.insufficientNote": [
      "Only {kept} epoch(s) remain in the time window; tracklets can only be insufficient/transient, and no motion is fabricated.",
      "Solo quedan {kept} época(s) en la ventana temporal; los tracklets solo pueden ser insuficientes/transitorios y no se fabrica movimiento.",
    ],

    /* ---------- catalog card ---------- */
    "catalog.pm.consistent": ["consistent", "consistente"],
    "catalog.pm.inconsistent": ["inconsistent", "inconsistente"],
    "catalog.pm.unknown": ["not assessed", "no evaluado"],
    "catalog.chip.sep": ["Separation {value}", "Separación {value}"],
    "catalog.chip.pm": ["Proper motion {value}", "Movimiento propio {value}"],
    "catalog.chip.candidates": ["Candidates {n}", "Candidatos {n}"],
    "catalog.ambiguous": [
      "There are <b>{n}</b> catalog sources within the matching radius; the system does <b>not auto-select</b> the brightest or nearest candidate.",
      "Hay <b>{n}</b> fuentes de catálogo dentro del radio de coincidencia; el sistema <b>no elige automáticamente</b> el candidato más brillante o más cercano.",
    ],
    "catalog.ambiguousMissing": ["Candidate details missing", "Faltan los detalles de los candidatos"],
    "catalog.ambiguousNote": [
      "{reason}. Verify candidates manually; ambiguous is not “confirmed”.",
      "{reason}. Verifica los candidatos manualmente; ambiguo no significa «confirmado».",
    ],
    "catalog.nomatch": [
      "The catalog query succeeded with <b>0 candidates</b>. This is only an “uncatalogued candidate (unconfirmed)”, not a discovery.",
      "La consulta al catálogo tuvo éxito con <b>0 candidatos</b>. Esto es solo un «candidato no catalogado (sin confirmar)», no un descubrimiento.",
    ],
    "catalog.nomatchNote": [
      "It may be a catalog gap, a galaxy/QSO, a spurious source, or a blend at 6.15″/px; unmatched does not mean a new object.",
      "Puede ser un hueco del catálogo, una galaxia/QSO, una fuente espuria o una mezcla a 6.15″/px; sin coincidencia no significa un objeto nuevo.",
    ],
    "catalog.unavailable": [
      "<b>Catalog unavailable; known/unknown status cannot be determined.</b>",
      "<b>Catálogo no disponible; no se puede determinar si es conocido o desconocido.</b>",
    ],
    "catalog.unavailableNote": [
      "{reason}; this motion analysis is unaffected, and the empty result was not treated as “uncatalogued”.",
      "{reason}; este análisis de movimiento no se ve afectado y el resultado vacío no se trató como «no catalogado».",
    ],
    "catalog.other": [
      "No catalog conclusion yet: <b>{reason}</b>.",
      "Aún no hay conclusión de catálogo: <b>{reason}</b>.",
    ],
    "catalog.otherNote": [
      "A measured target position and a usable catalog query are required; no guessing and no “new object” output.",
      "Se requieren una posición medida del objetivo y una consulta de catálogo utilizable; no se adivina ni se emite un «objeto nuevo».",
    ],
    "catalog.disclaimer": [
      "Unmatched does not mean a new object; this tool only compares against known catalogs and makes no discovery claim.",
      "Sin coincidencia no significa un objeto nuevo; esta herramienta solo compara con catálogos conocidos y no declara descubrimientos.",
    ],
    "catalog.badge.known_star": ["Known in catalog", "Conocido en catálogo"],
    "catalog.badge.knownGaia": ["Gaia DR3 known", "Gaia DR3 conocido"],
    "catalog.badge.ambiguous": ["Multiple candidates (ambiguous)", "Varios candidatos (ambiguo)"],
    "catalog.badge.uncatalogued_candidate": ["Uncatalogued candidate (unconfirmed)", "Candidato no catalogado (sin confirmar)"],
    "catalog.badge.catalog_unavailable": ["Catalog unavailable", "Catálogo no disponible"],
    "catalog.badge.insufficient_data": ["Insufficient data", "Datos insuficientes"],
    "catalog.badge.unknown": ["Unidentified", "No identificado"],

    /* ---------- status / quality labels ---------- */
    "quality.SATURATED": ["Saturated", "Saturada"],
    "quality.LOW_SNR": ["Low S/N", "S/N bajo"],
    "quality.EDGE": ["Edge", "Borde"],
    "quality.OUT_OF_FRAME": ["Out of frame", "Fuera del campo"],
    "quality.DUPLICATE": ["Duplicate", "Duplicada"],
    "quality.GOOD": ["Good quality", "Calidad buena"],
    "qualityFlag.SATURATED_SOURCE": ["Saturated source", "Fuente saturada"],
    "qualityFlag.FEW_EPOCHS": ["Few epochs", "Pocas épocas"],
    "qualityFlag.SHORT_BASELINE": ["Short baseline", "Línea base corta"],
    "qualityFlag.LARGE_CENTROID_ERROR": ["Large centroid error", "Error de centroide grande"],
    "qualityFlag.CATALOG_PRIOR_MOTION": ["Catalog-prior motion", "Movimiento por prior de catálogo"],
    "qualityFlag.MEASUREMENT_NOT_SIGNIFICANT": ["Measurement not significant", "Medición no significativa"],
    "qualityFlag.BLEND_SUSPECTED": ["Possible blend", "Posible mezcla"],
    "qualityFlag.BLEND_INDUCED_WANDER": ["Blend-induced wander", "Deriva inducida por mezcla"],
    "qualityFlag.IMPLAUSIBLE_PM": ["Implausible proper motion", "Movimiento propio no plausible"],
    "qualityFlag.TIME_WINDOW_EMPTY": ["Empty time window", "Ventana temporal vacía"],
    "qualityFlag.FETCH_FAILED": ["Epoch retrieval failed", "Falló la obtención de épocas"],
    "qualityFlag.SAMPLE_MODE_NO_TARGET_MEASUREMENT": ["Sample mode: no target measurement", "Modo muestra: sin medición del objetivo"],
    "qualityFlag.TARGET_NOT_DETECTED": ["Target not detected", "Objetivo no detectado"],
    "qualityFlag.TARGET_OUT_OF_FRAME": ["Target out of frame", "Objetivo fuera del encuadre"],
    "qualityFlag.MULTI_BAND": ["Multi-band", "Multibanda"],
    "qualityFlag.CATALOG_PARTIAL": ["Catalog partial", "Catálogo parcial"],
    "qualityFlag.NO_PM_CHECK": ["No proper-motion check", "Sin verificación de movimiento propio"],
    "qualityFlag.HIGH_PM": ["High proper motion", "Movimiento propio alto"],
    "qualityFlag.SINGLE_EPOCH": ["Single epoch", "Época única"],
    "qualityFlag.PM_INCONSISTENT": ["Proper motion inconsistent", "Movimiento propio inconsistente"],
    "qualityFlag.MULTIPLE_CANDIDATES": ["Multiple candidates", "Varios candidatos"],
    "qualityFlag.CATALOG_ID_MISSING": ["Catalog ID missing", "Falta el ID de catálogo"],
    "qualityFlag.CATALOG_OFFSET_TOO_LARGE": ["Catalog offset too large", "Desplazamiento de catálogo demasiado grande"],
    "qualityFlag.RUWE_UNAVAILABLE": ["RUWE unavailable", "RUWE no disponible"],
    "qualityFlag.HIGH_RUWE": ["High RUWE", "RUWE alto"],
    "qualityFlag.CATALOG_UNAVAILABLE": ["Catalog unavailable", "Catálogo no disponible"],

    /* ---------- API error codes ---------- */
    "apiError.generic": ["The service returned an error.", "El servicio devolvió un error."],
    "apiError.INVALID_FOV": ["Field of view must be between 0.02 and 3.0 degrees.", "El campo de visión debe estar entre 0.02 y 3.0 grados."],
    "apiError.INVALID_FOV_TOO_LARGE": ["Field of view is too large.", "El campo de visión es demasiado grande."],
    "apiError.INVALID_COORDINATE": ["Invalid coordinate.", "Coordenada no válida."],
    "apiError.MISSING_COORDINATE": ["RA and Dec must both be provided.", "Deben proporcionarse AR y Dec."],
    "apiError.INVALID_RA": ["RA must be in [0, 360) degrees.", "La AR debe estar en [0, 360) grados."],
    "apiError.INVALID_DEC": ["Dec must be in [-90, 90] degrees.", "La Dec debe estar en [-90, 90] grados."],
    "apiError.INVALID_QUERY": ["Query parameters are invalid.", "Los parámetros de consulta no son válidos."],
    "apiError.INVALID_TIME_WINDOW": ["The time window is invalid.", "La ventana temporal no es válida."],
    "apiError.UNKNOWN_TARGET": ["Unknown target.", "Objetivo desconocido."],
    "apiError.INVALID_MODE": ["Invalid data mode.", "Modo de datos no válido."],
    "apiError.INVALID_SOURCES": ["The source list is invalid.", "La lista de fuentes no es válida."],
    "apiError.BAD_JSON": ["The request body is not valid JSON.", "El cuerpo de la solicitud no es JSON válido."],
    "apiError.TOO_MANY_SOURCES": ["Too many sources in one batch (maximum 200).", "Demasiadas fuentes en un lote (máximo 200)."],
    "apiError.INVALID_SOURCE": ["One source in the batch is invalid.", "Una fuente del lote no es válida."],
    "apiError.INVALID_SIGMA": ["Position uncertainty is outside the allowed range.", "La incertidumbre de posición está fuera del rango permitido."],
    "apiError.SAMPLE_UNAVAILABLE": ["Offline sample files are missing.", "Faltan los archivos de muestra offline."],
    "apiError.DB_UNAVAILABLE": ["The epoch database is unavailable; no real epochs could be read.", "La base de datos de épocas no está disponible; no se pudieron leer épocas reales."],
    "apiError.NO_EPOCHS": ["No usable epochs for this sky field.", "No hay épocas utilizables para este campo celeste."],
    "apiError.FETCH_FAILED": ["Real-epoch cutout retrieval failed.", "Falló la obtención de los recortes de épocas reales."],
    "apiError.NO_LOCAL_COVERAGE": ["Only simulated epochs exist for this field; no real-epoch analysis is possible here.", "Solo existen épocas simuladas para este campo; aquí no es posible un análisis de épocas reales."],
    "apiError.INVALID_FORMAT": ["Invalid export format.", "Formato de exportación no válido."],
    "apiError.INTERNAL": ["Internal service error.", "Error interno del servicio."],

    /* ---------- catalogue reasons (B/A reason codes) ---------- */
    "reason.CATALOG_UNAVAILABLE": ["Catalog service unreachable or returned 503", "Servicio de catálogo inaccesible o devolvió 503"],
    "reason.TARGET_NOT_DETECTED": ["Target was not detected", "El objetivo no fue detectado"],
    "reason.FETCH_FAILED": ["Real cutout retrieval failed", "Falló la obtención del recorte real"],
    "reason.CATALOG_PARTIAL": ["Catalog coverage is incomplete; 0 candidates cannot be called “uncatalogued”", "La cobertura del catálogo está incompleta; 0 candidatos no permite decir «no catalogado»"],
    "reason.NO_CACHED_FIELD": ["Query point is outside cached catalog-field coverage", "El punto de consulta está fuera de la cobertura cacheada del campo"],
    "reason.CATALOG_UNREACHABLE": ["Catalog service unreachable", "Servicio de catálogo inaccesible"],
    "reason.MALFORMED_RESPONSE": ["Malformed catalog response", "Respuesta de catálogo mal formada"],
    "reason.SINGLE_EPOCH": ["Single epoch only; proper motion cannot be checked", "Solo una época; no se puede verificar el movimiento propio"],
    "reason.NO_CATALOG_MATCH": ["Catalog query succeeded but returned no candidates", "La consulta al catálogo tuvo éxito pero no devolvió candidatos"],
    "reason.MULTIPLE_CANDIDATES": ["Multiple catalog sources within the matching radius", "Varias fuentes de catálogo dentro del radio de coincidencia"],
    "reason.PM_INCONSISTENT": ["Position matches but proper motion is inconsistent", "La posición coincide pero el movimiento propio es inconsistente"],
    "reason.CATALOG_ID_MISSING": ["Candidate has no catalog_id", "El candidato no tiene catalog_id"],
    "reason.IDENTIFICATION_ERROR": ["Internal identification error; result degraded", "Error interno de identificación; resultado degradado"],
    "reason.NO_TARGET_MEASUREMENT": ["No measured target position", "Sin posición medida del objetivo"],
    "reason.SINGLE_CATALOG_MATCH": ["Position matches; catalog ID confirmed", "La posición coincide; ID de catálogo confirmado"],
    "reason.NO_PM_CHECK": ["Proper motion not checked (no usable PM)", "Movimiento propio no verificado (sin PM utilizable)"],
    "reason.HIGH_PM": ["High-proper-motion target", "Objetivo de alto movimiento propio"],
    "reason.CATALOG_CLIENT_ERROR": ["Catalog client error", "Error del cliente de catálogo"],
    "reason.CATALOG_OFFSET_TOO_LARGE": ["Catalog candidate offset exceeds the matching threshold", "El desplazamiento del candidato supera el umbral de coincidencia"],
    "reason.RUWE_UNAVAILABLE": ["RUWE unavailable", "RUWE no disponible"],
    "reason.RUWE_HIGH": ["RUWE above the project threshold", "RUWE por encima del umbral del proyecto"],
    "reason.INSUFFICIENT_DATA": ["Insufficient data for catalog matching", "Datos insuficientes para la comparación con catálogo"],
    "reason.DB_UNAVAILABLE": ["Database unavailable", "Base de datos no disponible"],
    "reason.NO_LOCAL_COVERAGE": ["No local real-epoch coverage", "Sin cobertura local de épocas reales"],
    "reason.INSUFFICIENT_EPOCHS": ["Too few real epochs", "Muy pocas épocas reales"],
    "reason.BASELINE_TOO_SHORT": ["Real-epoch baseline too short", "Línea base de épocas reales demasiado corta"],
    "reason.SAMPLE_MODE": ["Offline sample mode", "Modo muestra offline"],

    "notice.fetchFailedDetail": [
      "The catalog has {n} real epochs, but all cutout retrievals failed (0/{n}; the network or IRSA may be unavailable). No real determination was made; the images below are only a SIM demonstration. Retry when the network recovers.",
      "El catálogo tiene {n} épocas reales, pero falló la obtención de todos los recortes (0/{n}; puede que la red o IRSA no estén disponibles). No se hizo una determinación real; las imágenes siguientes son solo una demostración SIM. Vuelve a intentarlo cuando se recupere la red.",
    ],
    "notice.dbUnavailableDetail": [
      "The database is unavailable (DB_UNAVAILABLE): no real epochs were retrieved. The series below is a SIM demonstration, not real observations.",
      "La base de datos no está disponible (DB_UNAVAILABLE): no se obtuvieron épocas reales. La serie siguiente es una demostración SIM, no observaciones reales.",
    ],
    "notice.noLocalCoverageDetail": [
      "No real epochs matched this sky field (NO_LOCAL_COVERAGE). The series below is a SIM demonstration, not real observations.",
      "No hay épocas reales para este campo celeste (NO_LOCAL_COVERAGE). La serie siguiente es una demostración SIM, no observaciones reales.",
    ],
    "notice.hybridSingleEpochDetail": [
      "Only one real epoch was retrieved ({mjd}); at least two are needed to measure motion. The series below is a HYBRID validation series (real PSF/noise + injected target).",
      "Solo se obtuvo una época real ({mjd}); se necesitan al menos dos para medir movimiento. La serie siguiente es una serie de validación HYBRID (PSF/ruido reales + objetivo inyectado).",
    ],
    "notice.hybridShortBaselineDetail": [
      "Real epochs: {n}; the catalog baseline is only {days} days, insufficient to determine a motion pattern. The series below is a HYBRID validation series (real PSF/noise + injected target).",
      "Épocas reales: {n}; la línea base del catálogo es de solo {days} días, insuficiente para determinar un patrón de movimiento. La serie siguiente es una serie de validación HYBRID (PSF/ruido reales + objetivo inyectado).",
    ],
    "server.caveat.missingCoords": ["Coordinates/MJD are missing; the catalog cannot be queried.", "Faltan coordenadas/MJD; no se puede consultar el catálogo."],
    "server.reason.bNotWritten": ["B did not write catalog_match", "B no escribió catalog_match"],

    /* ---------- data-mode notices ---------- */
    "notice.mode.sample": [
      "Two offline real cutouts (M101 / Field A, different sky fields); parsing and rendering check only, no target measurement.",
      "Dos recortes reales offline (M101 / Campo A, campos celestes distintos); solo verificación de lectura y renderizado, sin medición del objetivo.",
    ],
    "notice.mode.real": [
      "Real epochs from the local catalog; IRSA static slices with relative calibration against reference stars.",
      "Épocas reales del catálogo local; cortes estáticos de IRSA con calibración relativa a estrellas de referencia.",
    ],
    "notice.mode.hybrid": [
      "Hybrid series: real SPHEREx PSF/noise/background plus an injected simulated target — not a real-epoch measurement.",
      "Serie híbrida: PSF/ruido/fondo reales de SPHEREx más un objetivo simulado inyectado — no es una medición de épocas reales.",
    ],
    "notice.mode.hybrid.insufficient": [
      "Only {real} real epoch(s) available, so motion cannot be measured; the series below is a validation series.",
      "Solo hay {real} época(s) reales, así que no se puede medir el movimiento; la serie siguiente es de validación.",
    ],
    "notice.mode.hybrid.shortBaseline": [
      "The real-epoch baseline is only {days} days, insufficient for a motion determination; the series below is a validation series.",
      "La línea base de épocas reales es de solo {days} días, insuficiente para determinar el movimiento; la serie siguiente es de validación.",
    ],
    "notice.mode.synthetic": [
      "Synthetic validation series generated from real SPHEREx PSF/noise/background — not real observations.",
      "Serie de validación sintética generada con PSF/ruido/fondo reales de SPHEREx — no son observaciones reales.",
    ],
    "notice.mode.synthetic.noCoverage": [
      "No real epochs matched this sky field; the series below is synthetic (SIM), not real observations.",
      "No hay épocas reales para este campo celeste; la serie siguiente es sintética (SIM), no observaciones reales.",
    ],
    "notice.mode.synthetic.dbUnavailable": [
      "The database is unavailable, so no real epochs were retrieved; the series below is a SIM demonstration, not real observations.",
      "La base de datos no está disponible, así que no se obtuvieron épocas reales; la serie siguiente es una demostración SIM, no observaciones reales.",
    ],
    "notice.mode.unavailable": [
      "Real epochs exist but cutout retrieval failed; no real measurement was made. The images below are only a SIM demonstration.",
      "Existen épocas reales pero falló la obtención de los recortes; no se hizo una medición real. Las imágenes siguientes son solo una demostración SIM.",
    ],
    "notice.counts.real": [
      "Fetched {succeeded}/{attempted} epochs; {detected} used in the fit; detection baseline {probe} days (catalog baseline {cat} days).",
      "Obtenidas {succeeded}/{attempted} épocas; {detected} usadas en el ajuste; línea base de detección {probe} días (línea base de catálogo {cat} días).",
    ],
    "notice.counts.hybrid": [
      "Validation series: {n} epochs; {detected} used in the fit; detection baseline {probe} days.",
      "Serie de validación: {n} épocas; {detected} usadas en el ajuste; línea base de detección {probe} días.",
    ],
    "notice.fallbackPrefix": ["Fallback: {reason}.", "Respaldo: {reason}."],

    "dir.noBatchIdentification": ["No batch identification", "Sin identificación en lote"],
    "server.track.missingEpochs": ["missing {n} epoch(s)", "faltan {n} época(s)"],
    "server.track.fourEpochs": ["4 epochs only; tracklet association is ambiguous", "Solo 4 épocas; la asociación de tracklets es ambigua"],
    "server.track.insufficientEpochs": ["insufficient epochs", "épocas insuficientes"],
    "server.track.residualOnly": ["residual-only track", "trayectoria solo residual"],
    "server.track.catalogLookupLimit": ["catalog lookup limit; not checked", "límite de consultas al catálogo; no verificado"],
    "server.track.catalogUnavailable": ["catalog unavailable; no known/unknown conclusion", "catálogo no disponible; sin conclusión conocido/desconocido"],
    "server.track.catalogPartial": ["catalog partial; D1 not assigned", "catálogo parcial; D1 no asignado"],
    "server.track.catalogAmbiguous": ["multiple catalog candidates; no hard assignment", "varios candidatos de catálogo; sin asignación definitiva"],
    "server.track.ruweUnavailable": ["RUWE unavailable; D1 is catalog identity, not high-confidence astrometry", "RUWE no disponible; D1 es identidad de catálogo, no astrometría de alta confianza"],

    /* ---------- server caveats (Chinese source texts translated) ---------- */
    "server.untranslated": [
      "A technical caveat is available in the full JSON export.",
      "Hay una advertencia técnica disponible en la exportación JSON completa.",
    ],
    "server.caveat.targetNotDetected": ["The target was not detected, so it cannot be compared with the catalog.", "No se detectó el objetivo, por lo que no puede compararse con el catálogo."],
    "server.caveat.fetchFailed": ["Real cutout retrieval failed; the target position cannot be confirmed.", "Falló la obtención del recorte real; no se puede confirmar la posición del objetivo."],
    "server.caveat.partialNoMatch": ["Catalog coverage is incomplete (partial); 0 candidates cannot be treated as “uncatalogued”.", "La cobertura del catálogo está incompleta (parcial); 0 candidatos no puede tratarse como «no catalogado»."],
    "server.caveat.catalogUnavailableShort": ["The catalog service is unavailable; known/unknown status cannot be determined.", "El servicio de catálogo no está disponible; no se puede determinar si es conocido o desconocido."],
    "server.caveat.catalogIdMissing": ["The catalog candidate lacks a stable ID, so “known” cannot be assigned safely.", "El candidato de catálogo carece de un ID estable; no se puede asignar «conocido» con seguridad."],
    "server.caveat.singleEpochPm": ["Only one epoch is available; proper motion / motion consistency cannot be checked.", "Solo hay una época; no se puede verificar el movimiento propio ni la consistencia del movimiento."],
    "server.caveat.lowSnr": ["Target S/N is too low for a reliable position match.", "El S/N del objetivo es demasiado bajo para una coincidencia de posición fiable."],
    "server.caveat.insufficientConditional": ["Insufficient identification conditions: {reason}.", "Condiciones de identificación insuficientes: {reason}."],
    "server.caveat.catalogUnavailableNoEmpty": ["The catalog service is unavailable; known/unknown status cannot be determined, and the empty result was not treated as “uncatalogued”.", "El servicio de catálogo no está disponible; no se puede determinar si es conocido o desconocido, y el resultado vacío no se trató como «no catalogado»."],
    "server.caveat.multipleCandidates": ["Multiple catalog candidates lie within the matching radius; the system does not automatically select the brightest or nearest one.", "Hay varios candidatos de catálogo dentro del radio de coincidencia; el sistema no elige automáticamente el más brillante o cercano."],
    "server.caveat.partialCandidatesMissing": ["Catalog coverage is incomplete (partial); the candidate list may be missing sources.", "La cobertura del catálogo está incompleta (parcial); la lista de candidatos puede carecer de fuentes."],
    "server.caveat.offsetAmbiguous": ["The nearest catalog candidate is offset by {sep}″, beyond the {limit}″ threshold; known/ambiguous cannot be assigned from it.", "El candidato de catálogo más cercano está desplazado {sep}″, más allá del umbral de {limit}″; no se puede asignar conocido/ambiguo a partir de él."],
    "server.caveat.offsetKnown": ["The catalog candidate is offset by {sep}″, beyond the {limit}″ threshold; “known” cannot be assigned from it.", "El candidato de catálogo está desplazado {sep}″, más allá del umbral de {limit}″; no se puede asignar «conocido» a partir de él."],
    "server.caveat.ruweMissing": ["B did not provide a usable RUWE/astrometric quality; the Gaia solution cannot be confirmed as reliable, so known is downgraded to ambiguous.", "B no proporcionó un RUWE/calidad astrométrica utilizables; no se puede confirmar que la solución de Gaia sea fiable, así que conocido se degrada a ambiguo."],
    "server.caveat.ruweHigh": ["Gaia RUWE={ruwe} > 2.0 (project threshold); the 5-parameter solution may be affected by a companion or contamination, so known is downgraded to ambiguous.", "RUWE de Gaia={ruwe} > 2.0 (umbral del proyecto); la solución de 5 parámetros puede verse afectada por una compañera o contaminación, así que conocido se degrada a ambiguo."],
    "server.caveat.pmInconsistent": ["The position matches but the measured proper motion disagrees with the catalog PM (>3σ); this may be a blend, an unresolved companion, or a wrong counterpart.", "La posición coincide pero el movimiento propio medido no concuerda con el PM del catálogo (>3σ); puede ser una mezcla, una compañera no resuelta o una contrapartida incorrecta."],
    "server.caveat.partialConclusion": ["Catalog coverage is incomplete (partial); the conclusion may be affected by missing sources.", "La cobertura del catálogo está incompleta (parcial); la conclusión puede verse afectada por fuentes faltantes."],
    "server.caveat.noPm": ["No usable measured proper motion (PM); known is based on position only and motion consistency was not checked.", "No hay un movimiento propio (PM) medido utilizable; conocido se basa solo en la posición y no se verificó la consistencia del movimiento."],
    "server.caveat.singleEpochKnown": ["Single-epoch observation only; proper motion cannot be checked, so known confidence is downgraded.", "Solo observación de una época; no se puede verificar el movimiento propio, así que la confianza de conocido se degrada."],
    "server.caveat.partialDegraded": ["Catalog coverage is incomplete (partial); candidates may be missing, so the conclusion is treated as degraded.", "La cobertura del catálogo está incompleta (parcial); pueden faltar candidatos, así que la conclusión se considera degradada."],
    "server.caveat.staleCache": ["A stale catalog cache was used; the result is reproducible but may not reflect the latest release.", "Se usó una caché de catálogo obsoleta; el resultado es reproducible pero puede no reflejar la versión más reciente."],
    "server.caveat.highPm": ["High-proper-motion target: the catalog PM was propagated to the observation epoch; residual offsets may still come from the catalog solution.", "Objetivo de alto movimiento propio: el PM del catálogo se propagó a la época de observación; los desvíos residuales aún pueden proceder de la solución de catálogo."],
    "server.caveat.excessNoise": ["Gaia astrometric_excess_noise={excess} mas > 2; astrometric quality needs caution and confidence was not upgraded automatically.", "astrometric_excess_noise de Gaia={excess} mas > 2; la calidad astrométrica requiere cautela y la confianza no se actualizó automáticamente."],
    "server.caveat.photVariable": ["Gaia phot_variable_flag={flag}; this is a catalog note only and does not constitute a variability conclusion for D.", "phot_variable_flag de Gaia={flag}; es solo una nota de catálogo y no constituye una conclusión de variabilidad para D."],
    "server.caveat.irColors": ["2MASS/WISE infrared values are used only for colour/cross-identification; no object classification is based on them alone.", "Los valores infrarrojos de 2MASS/WISE solo se usan para color/identificación cruzada; no se clasifica ningún objeto solo con ellos."],
    "server.caveat.noCandidates": ["The catalog query succeeded but returned no candidates; unmatched does not mean a new object, only an “uncatalogued candidate”.", "La consulta al catálogo tuvo éxito pero no devolvió candidatos; sin coincidencia no significa un objeto nuevo, solo un «candidato no catalogado»."],
    "server.caveat.possibleBlend": ["It may be a catalog gap, a galaxy/QSO, a spurious source, or a blend at 6.15″/px; higher resolution / deeper data are needed for review.", "Puede ser un hueco del catálogo, una galaxia/QSO, una fuente espuria o una mezcla a 6.15″/px; se necesitan datos de mayor resolución o más profundos para revisarlo."],
    "server.caveat.staleCacheCandidates": ["A stale catalog cache was used; missing candidates may come from the cache version.", "Se usó una caché de catálogo obsoleta; los candidatos faltantes pueden deberse a la versión de la caché."],
    "server.caveat.fewEpochsCandidate": ["Fewer than 2 epochs; proper motion / multi-epoch consistency was not checked, so candidate confidence is downgraded.", "Menos de 2 épocas; no se verificó el movimiento propio ni la consistencia multiépoca, así que la confianza del candidato se degrada."],
    "server.caveat.persistIncomplete": ["Persistence was not completed ({reason}); the identification result is still usable, persisted=false.", "No se completó la persistencia ({reason}); el resultado de identificación sigue siendo utilizable, persisted=false."],
    "server.caveat.pmErrorMissing": ["PM was provided without a PM error; the 3σ proper-motion consistency check was not performed, so known confidence is downgraded.", "Se proporcionó PM sin error de PM; no se realizó la verificación de consistencia a 3σ, así que la confianza de conocido se degrada."],
    "server.caveat.blend": ["The position series correlates with flux (Δχ²={dchi2}); a nearby star may be blending the source. The motion conclusion is retained, but the centroid may be affected.", "La serie de posición se correlaciona con el flujo (Δχ²={dchi2}); una estrella cercana puede estar mezclándose con la fuente. Se mantiene la conclusión de movimiento, pero el centroide puede verse afectado."],
    "server.caveat.blendDowngraded": ["The position trend correlates significantly with flux (Δχ²={dchi2}), and proper motion is not significant after removing the flux term; this is interpreted as centroid wander from a blend, not real motion.", "La tendencia de posición se correlaciona significativamente con el flujo (Δχ²={dchi2}) y el movimiento propio no es significativo tras eliminar el término de flujo; se interpreta como deriva del centroide por mezcla, no como movimiento real."],
    "server.caveat.multiBand": ["Multi-band epochs: fluxes are not directly comparable and variability was not assessed; astrometry remains valid.", "Épocas multibanda: los flujos no son directamente comparables y no se evaluó la variabilidad; la astrometría sigue siendo válida."],
    "server.caveat.implausiblePm": ["The fitted proper motion of {mu}″/yr exceeds the stellar prior limit (30″/yr), very likely a cross-epoch association error; the conclusion is downgraded.", "El movimiento propio ajustado de {mu}″/año supera el límite previo estelar (30″/año), muy probablemente un error de asociación entre épocas; la conclusión se degrada."],
    "server.caveat.catalogPrior": ["class=L comes from a Gaia PM prior; SPHEREx's μ measurement is not significant, and the catalog PM was not disguised as a measured motion.", "class=L procede de un prior de PM de Gaia; la medición de μ de SPHEREx no es significativa y el PM del catálogo no se disfrazó como movimiento medido."],
    "server.caveat.sample": ["Sample mode produces no motion conclusion.", "El modo muestra no produce ninguna conclusión de movimiento."],
    "server.caveat.emptyWindow": ["The time window is empty; the empty result is not treated as “no objects”.", "La ventana temporal está vacía; el resultado vacío no se trata como «sin objetos»."],
    "server.caveat.cascadeUnavailable": ["Catalog unavailable; cannot determine whether the source is known. This is not “unmatched”.", "Catálogo no disponible; no se puede determinar si la fuente es conocida. Esto no es «sin coincidencia»."],
    "server.caveat.trackUnavailable": ["Catalog unavailable; cannot determine whether the node is known. This is not “unmatched”.", "Catálogo no disponible; no se puede determinar si el nodo es conocido. Esto no es «sin coincidencia»."],

    /* ---------- cascade / tracks pages ---------- */
    "cascade.tagline": ["Cascade / residuals", "Cascada / residuales"],
    "cascade.subtitle": [
      "L1 high-recall detection → mask detected sources → residual low-threshold recheck",
      "Detección L1 de alta recuperación → enmascarar fuentes detectadas → reverificación de residuales con umbral bajo",
    ],
    "cascade.targetPreset": ["Target preset", "Objetivo predefinido"],
    "cascade.coordPlaceholder": ["custom coordinates", "coordenadas personalizadas"],
    "cascade.run": ["Run cascade detection", "Ejecutar detección en cascada"],
    "cascade.loadingTargets": ["Loading targets…", "Cargando objetivos…"],
    "cascade.warning": [
      "<b>Boundary</b>: residual candidates are only “secondary peaks outside L1”, <b>not new objects</b>; unmatched does not mean a new star. The cascade layer makes no motion/variability decision and no discovery claim.",
      "<b>Límite</b>: los candidatos residuales son solo «picos secundarios fuera de L1», <b>no objetos nuevos</b>; sin coincidencia no significa una estrella nueva. La capa de cascada no decide movimiento/variabilidad ni declara descubrimientos.",
    ],
    "cascade.footer.data": ["Data: SPHEREx level2 (local / DB epochs)", "Datos: SPHEREx level2 (épocas locales / BD)"],
    "cascade.footer.layers": ["Layers: L1 6σ ≤80/epoch · masked 2 px · residual 5σ ≤80/epoch", "Capas: L1 6σ ≤80/época · enmascarado 2 px · residual 5σ ≤80/época"],
    "cascade.footer.boundary": ["Residual candidates ≠ new objects", "Candidatos residuales ≠ objetos nuevos"],
    "cascade.summary.epochs": ["epochs", "épocas"],
    "cascade.summary.l1": ["L1 sources", "Fuentes L1"],
    "cascade.summary.masked": ["Masked", "Enmascaradas"],
    "cascade.summary.residual": ["Residual", "Residuales"],
    "cascade.summary.mode": ["Data mode", "Modo de datos"],
    "cascade.noteRunning": ["Fetching epochs and running the cascade layer…", "Obteniendo épocas y ejecutando la capa de cascada…"],
    "cascade.runError": ["Cascade unavailable: {msg}. No SIM/fabricated sources were shown.", "Cascada no disponible: {msg}. No se mostraron fuentes SIM/fabricadas."],
    "cascade.targetListFail": ["Target list unavailable: {msg}", "Lista de objetivos no disponible: {msg}"],
    "cascade.empty": ["No epochs.", "No hay épocas."],
    "cascade.layer.l1": ["L1 detections", "Detecciones L1"],
    "cascade.layer.residual": ["Residual candidates", "Candidatos residuales"],
    "cascade.layer.masked": ["Masked / already detected", "Enmascaradas / ya detectadas"],
    "cascade.layer.maskedCount": ["Masked / already detected ({n}) — reason=already_detected", "Enmascaradas / ya detectadas ({n}) — reason=already_detected"],
    "cascade.layer.none": ["No sources in this layer", "No hay fuentes en esta capa"],
    "cascade.layer.maskedNone": ["No L1 source needed masking in this epoch", "Ninguna fuente L1 necesitó enmascararse en esta época"],
    "cascade.layer.showing": ["Showing the first 80 of {n}; counts cover all.", "Mostrando las primeras 80 de {n}; los recuentos cubren todas."],
    "cascade.layer.failed": ["Cascade failed for this epoch: {err}", "La cascada falló en esta época: {err}"],
    "cascade.selected": ["Selected source", "Fuente seleccionada"],
    "cascade.identifyButton": ["Identify with /api/identify", "Identificar con /api/identify"],
    "cascade.identifyHint": ["Catalog identity only; residual candidates are not new objects.", "Solo identidad de catálogo; los candidatos residuales no son objetos nuevos."],
    "cascade.querying": ["Querying catalog…", "Consultando catálogo…"],
    "cascade.identifyFail": ["identify request failed", "la solicitud de identify falló"],
    "cascade.identifyRequestError": ["identify request error", "error en la solicitud de identify"],
    "cascade.identifyUnavailable": ["Catalog unavailable; cannot determine whether the source is known. This is not “unmatched”.", "Catálogo no disponible; no se puede determinar si la fuente es conocida. Esto no es «sin coincidencia»."],
    "cascade.sourceTable.stage": ["stage", "etapa"],
    "cascade.sourceTable.quality": ["quality", "calidad"],
    "cascade.stage.l1": ["L1 detections", "Detecciones L1"],
    "cascade.stage.residual": ["Residual candidates", "Candidatos residuales"],
    "cascade.stage.masked": ["Masked", "Enmascaradas"],

    "tracks.tagline": ["Cross-epoch tracks", "Trayectorias entre épocas"],
    "tracks.subtitle": [
      "L1 + residual nodes → tracklet → D0–D4 candidate grading",
      "Nodos L1 + residuales → tracklet → clasificación de candidatos D0–D4",
    ],
    "tracks.run": ["Run track search", "Ejecutar búsqueda de trayectorias"],
    "tracks.loadingTargets": ["Loading targets…", "Cargando objetivos…"],
    "tracks.warning": [
      "<b>Boundary</b>: track candidates are not new objects; only D5 requires manual/follow-up confirmation. D0–D4 are candidate grades, not a discovery claim; at 4 epochs the association itself is ambiguous.",
      "<b>Límite</b>: los candidatos de trayectoria no son objetos nuevos; solo D5 requiere confirmación manual o de seguimiento. D0–D4 son grados de candidato, no una declaración de descubrimiento; con 4 épocas la asociación es ambigua.",
    ],
    "tracks.footer.levels": [
      "Layers: D0 artifact · D1 known catalog · D2 not implemented · D3 unknown moving · D4 unknown stationary",
      "Capas: D0 artefacto · D1 catálogo conocido · D2 no implementado · D3 desconocido en movimiento · D4 desconocido estacionario",
    ],
    "tracks.footer.boundary": [
      "Track candidates ≠ new objects; D5 requires manual/follow-up confirmation",
      "Candidatos de trayectoria ≠ objetos nuevos; D5 requiere confirmación manual o de seguimiento",
    ],
    "tracks.empty": [
      "No tracklets. No tracks are fabricated when data are insufficient or nodes are absent.",
      "No hay tracklets. No se fabrican trayectorias cuando faltan datos o nodos.",
    ],
    "tracks.noteRunning": ["Fetching epochs, running cascade and associating tracklets…", "Obteniendo épocas, ejecutando la cascada y asociando tracklets…"],
    "tracks.runError": ["Tracks unavailable: {msg}. No SIM/fabricated tracks were shown.", "Trayectorias no disponibles: {msg}. No se mostraron trayectorias SIM/fabricadas."],
    "tracks.targetListFail": ["Target list unavailable: {msg}", "Lista de objetivos no disponible: {msg}"],
    "tracks.expand": ["Nodes: {n} · expand", "Nodos: {n} · expandir"],
    "tracks.confidence": ["confidence", "confianza"],
    "tracks.identifyButton": ["Identify", "Identificar"],
    "tracks.querying": ["Querying catalog…", "Consultando catálogo…"],
    "tracks.identifyFail": ["identify request failed", "la solicitud de identify falló"],
    "tracks.identifyRequestError": ["identify request error", "error en la solicitud de identify"],
    "tracks.identifyUnavailable": [
      "Catalog unavailable; cannot determine whether the node is known. This is not “unmatched”.",
      "Catálogo no disponible; no se puede determinar si el nodo es conocido. Esto no es «sin coincidencia».",
    ],
    "tracks.catalogLabel": ["catalog", "catálogo"],
    "tracks.level.D0": ["D0 artifact", "D0 artefacto"],
    "tracks.level.D1": ["D1 known catalog", "D1 catálogo conocido"],
    "tracks.level.D2": ["D2 not implemented", "D2 no implementado"],
    "tracks.level.D3": ["D3 unknown moving", "D3 desconocido en movimiento"],
    "tracks.level.D4": ["D4 unknown stationary", "D4 desconocido estacionario"],
  };

  /* Build per-language tables from the paired table above. */
  const MESSAGES = { en: {}, es: {} };
  Object.keys(STRINGS).forEach((key) => {
    MESSAGES.en[key] = STRINGS[key][0];
    MESSAGES.es[key] = STRINGS[key][1];
  });

  let currentLang = DEFAULT_LANG;
  const listeners = [];

  function normalizeLang(value) {
    const v = String(value || "").trim().toLowerCase();
    return SUPPORTED_LANGS.includes(v) ? v : null;
  }

  function hasKey(key) {
    return Object.prototype.hasOwnProperty.call(MESSAGES.en, key);
  }

  function interpolate(text, params) {
    if (!params) return text;
    return String(text).replace(/\{(\w+)\}/g, (match, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
    );
  }

  function t(key, params) {
    const table = MESSAGES[currentLang] || MESSAGES.en;
    let value = table[key];
    if (value == null) value = MESSAGES.en[key];
    if (value == null) return key;
    return interpolate(value, params);
  }

  function safeStorageGet() {
    try {
      if (typeof localStorage !== "undefined") return localStorage.getItem(STORAGE_KEY);
    } catch (err) { /* storage may be blocked */ }
    return null;
  }

  function safeStorageSet(lang) {
    try {
      if (typeof localStorage !== "undefined") localStorage.setItem(STORAGE_KEY, lang);
    } catch (err) { /* storage may be blocked */ }
  }

  function resolveLang(search, stored) {
    const query = new URLSearchParams(search == null ? "" : String(search));
    return normalizeLang(query.get("lang")) || normalizeLang(stored) || DEFAULT_LANG;
  }

  function syncSelectors() {
    if (typeof document === "undefined") return;
    document.querySelectorAll("[data-lang-select]").forEach((el) => {
      el.value = currentLang;
    });
  }

  function pageTitle() {
    if (typeof document === "undefined") return;
    const page = (document.body && document.body.dataset && document.body.dataset.page) || "home";
    const key = `page.${page}.title`;
    if (hasKey(key)) document.title = t(key);
  }

  function apply(root) {
    if (typeof document === "undefined") return;
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.dataset.i18n;
      if (hasKey(key)) el.textContent = t(key);
    });
    scope.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const key = el.dataset.i18nHtml;
      if (hasKey(key)) el.innerHTML = t(key);
    });
    scope.querySelectorAll("[data-i18n-title]").forEach((el) => {
      const key = el.dataset.i18nTitle;
      if (hasKey(key)) el.title = t(key);
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.dataset.i18nPlaceholder;
      if (hasKey(key)) el.placeholder = t(key);
    });
    scope.querySelectorAll("[data-i18n-aria-label]").forEach((el) => {
      const key = el.dataset.i18nAriaLabel;
      if (hasKey(key)) el.setAttribute("aria-label", t(key));
    });
  }

  function bindSelectors() {
    if (typeof document === "undefined") return;
    document.querySelectorAll("[data-lang-select]").forEach((el) => {
      if (el.dataset.i18nBound === "1") return;
      el.dataset.i18nBound = "1";
      el.addEventListener("change", () => setLang(el.value));
    });
  }

  function notify() {
    listeners.forEach((fn) => {
      try { fn(currentLang); } catch (err) { console.error(err); }
    });
    if (typeof document !== "undefined" && typeof CustomEvent !== "undefined") {
      document.dispatchEvent(new CustomEvent("movescope:langchange", { detail: { lang: currentLang } }));
    }
  }

  function setLang(lang, options) {
    const next = normalizeLang(lang);
    if (!next) return currentLang;
    const opts = options || {};
    currentLang = next;
    if (opts.persist !== false) safeStorageSet(next);
    if (typeof document !== "undefined") {
      document.documentElement.lang = next;
      syncSelectors();
      pageTitle();
      apply(document);
    }
    if (opts.updateUrl !== false && typeof location !== "undefined" && typeof history !== "undefined") {
      try {
        const u = new URL(location.href);
        u.searchParams.set("lang", next);
        history.replaceState(null, "", u.toString());
      } catch (err) { /* ignore malformed URL */ }
    }
    notify();
    return currentLang;
  }

  function init(options) {
    const opts = options || {};
    const search = opts.search != null ? opts.search : (typeof location !== "undefined" ? location.search : "");
    const stored = opts.stored != null ? opts.stored : safeStorageGet();
    const fromUrl = normalizeLang(new URLSearchParams(String(search)).get("lang"));
    currentLang = resolveLang(search, stored);
    if (fromUrl && opts.persist !== false) safeStorageSet(fromUrl);
    if (typeof document !== "undefined") {
      document.documentElement.lang = currentLang;
      bindSelectors();
      syncSelectors();
      pageTitle();
      apply(document);
    }
    return currentLang;
  }

  function onChange(fn) {
    if (typeof fn === "function") listeners.push(fn);
  }

  /* ---------- localise scientific prose returned by the API ---------- */

  const SERVER_EXACT = {
    "星表不可用": "catalog.badge.catalog_unavailable",
    "B 未写入 catalog_match": "server.reason.bNotWritten",
    "未匹配不等于新天体；本工具只做已知/未知比对，不做发现声明。": "catalog.disclaimer",
    "星表服务不可用，无法判断已知/未知。": "server.caveat.catalogUnavailableShort",
    "目标没有被探测到，无法做星表比对。": "server.caveat.targetNotDetected",
    "真实切片取数失败，无法确认目标位置。": "server.caveat.fetchFailed",
    "星表覆盖不完整（partial），0 候选不能当作“未收录”。": "server.caveat.partialNoMatch",
    "星表候选缺少稳定 ID，不能安全地给 known。": "server.caveat.catalogIdMissing",
    "仅单期观测，无法核对自行/运动一致性。": "server.caveat.singleEpochPm",
    "目标 S/N 不足，位置匹配不可靠。": "server.caveat.lowSnr",
    "星表服务不可用，无法判断已知/未知；没有把空结果当成“未收录”。": "server.caveat.catalogUnavailableNoEmpty",
    "匹配半径内有多个星表候选；系统不自动选择最亮或最近的一个。": "server.caveat.multipleCandidates",
    "星表覆盖不完整（partial），候选列表可能缺源。": "server.caveat.partialCandidatesMissing",
    "B 未提供可用的 RUWE/天体测量质量；无法确认 Gaia 解可靠，known 降级为 ambiguous。": "server.caveat.ruweMissing",
    "位置匹配但实测自行与星表 PM 不一致（>3σ）；可能是混合、未解析伴星或错误对应体。": "server.caveat.pmInconsistent",
    "星表覆盖不完整（partial），结论可能受缺源影响。": "server.caveat.partialConclusion",
    "未提供或未测到可用的自行（PM）；known 仅基于位置匹配，未核对运动一致性。": "server.caveat.noPm",
    "仅单期观测，无法核对自行；known 置信度降级。": "server.caveat.singleEpochKnown",
    "星表覆盖不完整（partial），候选可能缺失；结论按降级处理。": "server.caveat.partialDegraded",
    "使用过期的星表缓存；结果可复现但可能不是最新 release。": "server.caveat.staleCache",
    "高自行目标：已按星表 PM 传播到观测历元；残余偏差仍可能来自星表解。": "server.caveat.highPm",
    "2MASS/WISE 红外数值仅作颜色/交叉证认；不单独据此做天体分类。": "server.caveat.irColors",
    "星表查询成功但没有候选；未匹配不等于新天体，只是“未收录候选”。": "server.caveat.noCandidates",
    "可能是星表遗漏、星系/QSO、伪源或 6.15″/px 下的混合；需要更高分辨率/更深数据复核。": "server.caveat.possibleBlend",
    "使用过期星表缓存；候选缺失可能来自缓存版本。": "server.caveat.staleCacheCandidates",
    "少于 2 期观测，未核对自行/多历元一致性；候选置信度降级。": "server.caveat.fewEpochsCandidate",
    "多波段历元：流量不可直接比较，未评估变光；天体测量仍有效。": "server.caveat.multiBand",
    "class=L 来自 Gaia PM 先验；SPHEREx 的 μ 测量不显著，未把星表 PM 伪装成实测运动。": "server.caveat.catalogPrior",
    "样本模式不产生运动结论。": "server.caveat.sample",
    "时间窗口为空；不把空结果当成“没有天体”。": "server.caveat.emptyWindow",
    "缺少坐标/MJD，无法查询星表。": "server.caveat.missingCoords",
    "星表不可用，无法判断该源是否已知；这不是“未匹配”。": "server.caveat.cascadeUnavailable",
    "星表不可用，不能判断节点是否已知；这不是“未匹配”。": "server.caveat.trackUnavailable",
    "未匹配不等于新天体；星表不可用不代表“未收录”。": "server.caveat.catalogUnavailableNoEmpty",
    "在预测位置附近没有达到 3σ 的探测": "result.evidence.notDetected",
    "时间窗口内没有可用历元；没有生成源表、模型或运动结论。": "result.evidence.emptyWindow",
    "两份离线样本来自 M101 与场 A，是两个不同天区": "result.evidence.sample1",
    "样本只用于验证 FITS 解析、HDU 裁剪与渲染链路": "result.evidence.sample2",
    "样本模式不对任何目标做质心/位移/变光测量": "result.evidence.sample3",
    "真实历元存在但切片取数失败；未做判定。": "notice.mode.unavailable",
    "若后续更深的观测或更大的视场里出现该目标，则本结论失效。": "result.falsifier.notDetected",
    "换一个包含历元的时间窗口后重试。": "result.falsifier.emptyWindow",
    "数据不足以判断这个天体的运动规律。需要更多历元（至少 3 个，最好跨 6 个月）。": "result.public.u",
  };

  const SERVER_EN_EXACT = {
    "no batch identification": "dir.noBatchIdentification",
    "catalog lookup limit; not checked": "server.track.catalogLookupLimit",
    "catalog unavailable; no known/unknown conclusion": "server.track.catalogUnavailable",
    "catalog partial; D1 not assigned": "server.track.catalogPartial",
    "multiple catalog candidates; no hard assignment": "server.track.catalogAmbiguous",
    "insufficient_epochs": "server.track.insufficientEpochs",
    "residual-only track": "server.track.residualOnly",
    "RUWE unavailable; D1 is catalog identity, not high-confidence astrometry": "server.track.ruweUnavailable",
    "4 epochs only; tracklet association is ambiguous": "server.track.fourEpochs",
  };

  function serverEnglish(text) {
    if (Object.prototype.hasOwnProperty.call(SERVER_EN_EXACT, text)) return t(SERVER_EN_EXACT[text]);
    const m = text.match(/^missing (\d+) epoch\(s\)$/);
    if (m) return t("server.track.missingEpochs", { n: m[1] });
    return text;
  }

  const SERVER_RULES = [
    {
      re: /^位移 ([\d.]+) ± ([\d.]+) 角秒，方向 ([\d.]+)°（北起东向）$/,
      key: "result.evidence.displacement",
      params: (m) => ({ disp: m[1], err: m[2], pa: m[3] }),
    },
    {
      re: /^拟合自行 ([\d.]+) mas\/yr（≈([\d.]+) 像素\/年）$/,
      key: "result.evidence.mu",
      params: (m) => ({ mu: m[1], px: m[2] }),
    },
    { re: /^位置变化与 0 相容（<5σ）$/, key: "result.evidence.consistentZero" },
    {
      re: /^亮度变化幅度 ([\d.]+) 等$/,
      key: "result.evidence.amp",
      params: (m) => ({ amp: m[1] }),
    },
    {
      re: /^周期候选 ([\d.]+) 天$/,
      key: "result.evidence.period",
      params: (m) => ({ period: m[1] }),
    },
    {
      re: /^有效历元 (\d+) 个，基线 ([\d.]+) 天$/,
      key: "result.evidence.epochs",
      params: (m) => ({ n: m[1], days: m[2] }),
    },
    {
      re: /^真实历元取数失败：成功 (\d+)\/(\d+) 期$/,
      key: "result.evidence.fetchFailed",
      params: (m) => ({ succeeded: m[1], attempted: m[2] }),
    },
    { re: /^共尝试 (\d+) 期$/, key: "result.evidence.attemptedEpochs", params: (m) => ({ n: m[1] }) },
    {
      re: /^(\d+) 期目标预测位置在切片外（(.+)…）$/,
      key: "result.evidence.outOfFrame",
      params: (m) => ({ n: m[1], ids: m[2] }),
    },
    {
      re: /^(\d+) 期目标预测位置在切片外，已跳过：(.+)$/,
      key: "result.evidence.outOfFrameSkipped",
      params: (m) => ({ n: m[1], ids: m[2] }),
    },
    {
      re: /^最近星表候选偏离 ([\d.]+)″，超过 ([\d.]+)″ 门限，不能据此给 known\/ambiguous。$/,
      key: "server.caveat.offsetAmbiguous",
      params: (m) => ({ sep: m[1], limit: m[2] }),
    },
    {
      re: /^星表候选偏离 ([\d.]+)″，超过 ([\d.]+)″ 门限，不能据此给 known。$/,
      key: "server.caveat.offsetKnown",
      params: (m) => ({ sep: m[1], limit: m[2] }),
    },
    {
      re: /^Gaia RUWE=([\d.]+) > 2\.0（项目阈值）；5 参数解可能受伴星\/污染影响，known 降级为 ambiguous。$/,
      key: "server.caveat.ruweHigh",
      params: (m) => ({ ruwe: m[1] }),
    },
    {
      re: /^Gaia astrometric_excess_noise=([\d.]+) mas > 2；天体测量质量需谨慎，未据此自动升级置信度。$/,
      key: "server.caveat.excessNoise",
      params: (m) => ({ excess: m[1] }),
    },
    {
      re: /^Gaia phot_variable_flag=(.+)；仅作目录备注，不构成 D 的变星结论。$/,
      key: "server.caveat.photVariable",
      params: (m) => ({ flag: m[1] }),
    },
    {
      re: /^持久化未完成（(.+)）；本次识别结果仍可用，persisted=false。$/,
      key: "server.caveat.persistIncomplete",
      params: (m) => ({ reason: serverText(m[1]) }),
    },
    {
      re: /^提供了 PM 但未提供 PM 误差；未做 3σ 自行一致性判定，known 置信度降级。$/,
      key: "server.caveat.pmErrorMissing",
    },
    {
      re: /^位置序列与流量相关（Δχ²=([\d.]+)）：可能有邻星混叠，运动结论保留，但质心可能受影响。$/,
      key: "server.caveat.blend",
      params: (m) => ({ dchi2: m[1] }),
    },
    {
      re: /^位置趋势与流量显著相关（Δχ²=([\d.]+)），且扣除流量项后自行不显著；判断为邻星混叠导致的质心漂移，不是真实移动。$/,
      key: "server.caveat.blendDowngraded",
      params: (m) => ({ dchi2: m[1] }),
    },
    {
      re: /^拟合自行 ([\d.]+)″\/yr 超过恒星先验上限（30″\/yr），极可能是跨历元关联错误，结论降级。$/,
      key: "server.caveat.implausiblePm",
      params: (m) => ({ mu: m[1] }),
    },
    {
      re: /^目录有 (\d+) 期真实历元，但切片取数全部失败（0\/(\d+)，可能网络或 IRSA 不可用）；未做真实判定，以下画面仅为 SIM 演示，请恢复网络后重试。$/,
      key: "notice.fetchFailedDetail",
      params: (m) => ({ n: m[1] }),
    },
    {
      re: /^数据库不可用（DB_UNAVAILABLE）：没有取到真实历元；以下为 SIM 演示，不是真实观测。$/,
      key: "notice.dbUnavailableDetail",
    },
    {
      re: /^该天区没有匹配到真实历元（NO_LOCAL_COVERAGE）；以下为 SIM 演示，不是真实观测。$/,
      key: "notice.noLocalCoverageDetail",
    },
    {
      re: /^目录\/数据库有 (\d+) 期真实历元，切片只成功取回 1 期（([\d.]+)），不足 2 期；以下为验证时序（HYBRID：1 期 REAL \+ (\d+) 期 SIM）。$/,
      key: "notice.hybridSingleEpochDetail",
      params: (m) => ({ mjd: m[2] }),
    },
    {
      re: /^真实历元 (\d+) 期，目录基线仅 ([\d.]+) 天，不足以判定运动规律；以下为验证时序（HYBRID，真实 PSF\/噪声 \+ 注入目标）。$/,
      key: "notice.hybridShortBaselineDetail",
      params: (m) => ({ n: m[1], days: m[2] }),
    },
    {
      re: /^识别条件不足：(.+)。$/,
      key: "server.caveat.insufficientConditional",
      params: (m) => ({ reason: m[1] }),
    },
  ];

  function hasChinese(text) {
    return /[\u4e00-\u9fff]/.test(String(text || ""));
  }

  function serverText(text) {
    if (text == null) return "";
    const value = String(text);
    if (!hasChinese(value)) return serverEnglish(value);
    if (Object.prototype.hasOwnProperty.call(SERVER_EXACT, value)) {
      const key = SERVER_EXACT[value];
      if (key == null) return value;
      return t(key);
    }
    for (const rule of SERVER_RULES) {
      const m = value.match(rule.re);
      if (m) return t(rule.key, rule.params ? rule.params(m) : null);
    }
    console.warn("[i18n] untranslated server text:", value);
    return t("server.untranslated");
  }

  function serverList(list) {
    return (Array.isArray(list) ? list : []).map((item) => serverText(item)).filter(Boolean);
  }

  function errorText(err) {
    if (err == null) return t("apiError.generic");
    if (typeof err === "string") return serverText(err);
    const code = err.code;
    const key = code ? `apiError.${code}` : null;
    if (key && hasKey(key)) return t(key);
    const msg = err.message || err.code || "";
    const localized = serverText(msg);
    if (localized && localized !== t("server.untranslated")) return localized;
    return t("apiError.generic");
  }

  function catalogLabel(status, release) {
    if (status === "known_star" && String(release || "").toLowerCase().includes("gaia")) {
      return t("catalog.badge.knownGaia");
    }
    const key = `catalog.badge.${status || "unknown"}`;
    return hasKey(key) ? t(key) : t("catalog.badge.unknown");
  }

  function catalogReason(ident) {
    if (!ident) return "";
    let code = ident.reason || "";
    if (code === "CATALOG_UNAVAILABLE") code = ident.catalog_reason || code;
    const key = `reason.${code}`;
    return hasKey(key) ? t(key) : (code || "");
  }

  function qualityText(src) {
    const flags = src && Array.isArray(src.quality) ? src.quality : [];
    if (!flags.length) return t("quality.GOOD");
    return flags.map((flag) => {
      const key = `quality.${flag}`;
      return hasKey(key) ? t(key) : String(flag);
    }).join(" · ");
  }

  function qualityFlag(flag) {
    const key = `qualityFlag.${flag}`;
    return hasKey(key) ? t(key) : String(flag);
  }

  function modeNote(data, options) {
    const opts = options || {};
    const d = data || {};
    const mode = d.mode || "synthetic";
    const reason = d.fallback_reason || null;
    const n = Number(d.n_epochs || 0);
    const real = Number(d.real_epochs || 0);
    const attempted = Number(d.fetch_attempted || 0);
    const succeeded = Number(d.fetch_succeeded || 0);
    const detected = d.detected_epochs != null ? Number(d.detected_epochs) : null;
    const probe = d.detection_baseline_days != null ? Number(d.detection_baseline_days) : null;
    const cat = d.catalog_baseline_days != null ? Number(d.catalog_baseline_days) : 0;
    const parts = [];
    if (mode === "sample") {
      parts.push(t("notice.mode.sample"));
    } else if (mode === "real") {
      parts.push(t("notice.mode.real"));
      if (opts.includeCounts !== false && attempted > 0) {
        parts.push(t("notice.counts.real", {
          succeeded,
          attempted,
          detected: detected == null ? 0 : detected,
          probe: probe == null ? "—" : probe.toFixed(0),
          cat: cat ? cat.toFixed(0) : "—",
        }));
      }
    } else if (mode === "hybrid") {
      if (real <= 1 || reason === "INSUFFICIENT_EPOCHS") {
        parts.push(t("notice.mode.hybrid.insufficient", { real }));
      } else if (reason === "BASELINE_TOO_SHORT") {
        parts.push(t("notice.mode.hybrid.shortBaseline", { days: cat ? cat.toFixed(0) : "—" }));
      } else {
        parts.push(t("notice.mode.hybrid"));
      }
      if (opts.includeCounts !== false && detected != null) {
        parts.push(t("notice.counts.hybrid", {
          n,
          detected,
          probe: probe == null ? "—" : probe.toFixed(0),
        }));
      }
    } else if (mode === "unavailable") {
      parts.push(t("notice.mode.unavailable"));
    } else {
      parts.push(t(
        reason === "NO_LOCAL_COVERAGE"
          ? "notice.mode.synthetic.noCoverage"
          : reason === "DB_UNAVAILABLE"
            ? "notice.mode.synthetic.dbUnavailable"
            : "notice.mode.synthetic"
      ));
    }
    if (reason && mode !== "real" && mode !== "sample" && mode !== "unavailable") {
      const key = `reason.${reason}`;
      if (hasKey(key)) parts.push(t("notice.fallbackPrefix", { reason: t(key) }));
    }
    return parts.filter(Boolean).join(" ");
  }

  function resultView(result, options) {
    const r = result || {};
    const opts = options || {};
    const quality = Array.isArray(r.quality) ? r.quality : [];
    const prior = r.catalog_motion && r.catalog_motion.basis === "catalog_prior";
    const suspicious = quality.includes("IMPLAUSIBLE_PM");
    const notDetected = quality.includes("TARGET_NOT_DETECTED");
    const emptyWindow = quality.includes("TIME_WINDOW_EMPTY");
    const sample = quality.includes("SAMPLE_MODE_NO_TARGET_MEASUREMENT");
    const fetchFailed = quality.includes("FETCH_FAILED");
    const fp = r.fingerprint || {};
    const cls = r.class && hasKey(`class.${r.class}.label`) ? r.class : "U";
    const n = Number(fp.n_epochs != null ? fp.n_epochs : r.detected_epochs || 0);
    const days = Number(fp.baseline_days || r.baseline_days || 0);
    const disp = Number(fp.displacement_arcsec || 0);
    const err = Number(fp.displacement_err_arcsec || 0);
    const pa = Number(fp.pa_disp_deg || 0);
    const mu = Number(fp.mu_mas_yr || 0);
    const px = Number(fp.mu_px_yr || 0);
    const amp = Number(fp.amp_mag || 0);
    const accel = Number(fp.accel_sig || 0);
    const period = Number(fp.period_d || 0);

    let label;
    let publicText;
    let falsifier;
    let evidence;

    if (sample) {
      label = t("result.label.sample");
      publicText = t("result.public.sample");
      falsifier = "";
      evidence = serverList(r.evidence);
    } else if (emptyWindow) {
      label = t("result.label.emptyWindow");
      publicText = t("result.public.emptyWindow");
      falsifier = t("result.falsifier.emptyWindow");
      evidence = serverList(r.evidence);
    } else if (fetchFailed) {
      label = t("result.label.fetchFailed");
      publicText = t("result.public.fetchFailed");
      falsifier = t("result.falsifier.fetchFailed");
      evidence = serverList(r.evidence);
    } else if (notDetected) {
      label = t("result.label.notDetected");
      publicText = t("result.public.notDetected");
      falsifier = t("result.falsifier.notDetected");
      evidence = serverList(r.evidence);
    } else if (suspicious) {
      label = t("result.label.associationSuspicious");
      publicText = t("result.public.associationSuspicious", { mu: (mu / 1000).toFixed(1) });
      falsifier = t("result.falsifier.u");
      evidence = serverList(r.evidence);
    } else if (prior) {
      label = t("result.label.catalogPrior");
      publicText = t("result.public.catalogPrior");
      falsifier = t("result.falsifier.u");
      evidence = buildEvidence(r, fp);
    } else if (cls === "V") {
      label = t("class.V.label");
      publicText = t("result.public.v", { n, amp: amp.toFixed(2) });
      falsifier = t("result.falsifier.v");
      evidence = buildEvidence(r, fp);
    } else if (cls === "L") {
      label = t("class.L.label");
      publicText = t("result.public.l", {
        days: days.toFixed(0), disp: disp.toFixed(2), pa: pa.toFixed(0), mu: (mu / 1000).toFixed(2),
      });
      falsifier = t("result.falsifier.l");
      evidence = buildEvidence(r, fp);
    } else if (cls === "A") {
      label = t("class.A.label");
      publicText = t("result.public.a", { disp: disp.toFixed(2), accel: accel.toFixed(1) });
      falsifier = t("result.falsifier.a");
      evidence = buildEvidence(r, fp);
    } else if (cls === "S") {
      label = t("class.S.label");
      publicText = (disp > 0.5 && err > 0)
        ? t("result.public.sDetailed", { n, disp: disp.toFixed(2), err: err.toFixed(2) })
        : t("result.public.s", { n });
      falsifier = t("result.falsifier.u");
      evidence = buildEvidence(r, fp);
    } else if (cls === "P" || cls === "O") {
      label = t(`class.${cls}.label`);
      publicText = Number(r.detected_epochs || 0) === 1
        ? t("result.public.uOneEpoch")
        : t("result.public.u");
      falsifier = t("result.falsifier.u");
      evidence = buildEvidence(r, fp);
    } else {
      label = t("class.U.label");
      publicText = Number(r.detected_epochs || 0) === 1
        ? t("result.public.uOneEpoch")
        : t("result.public.u");
      falsifier = t("result.falsifier.u");
      evidence = buildEvidence(r, fp);
    }

    if (quality.includes("TARGET_OUT_OF_FRAME")) evidence = serverList(r.evidence);
    const caveats = serverList(r.caveats);
    return {
      label,
      publicText,
      evidence: opts.rawEvidence ? serverList(r.evidence) : evidence,
      falsifier: r.falsifier && !hasChinese(r.falsifier) && opts.keepEnglish ? r.falsifier : falsifier,
      caveats,
    };
  }

  function buildEvidence(r, fp) {
    const out = [];
    if (r.moving) {
      out.push(t("result.evidence.displacement", {
        disp: Number(fp.displacement_arcsec || 0).toFixed(2),
        err: Number(fp.displacement_err_arcsec || 0).toFixed(2),
        pa: Number(fp.pa_disp_deg || 0).toFixed(0),
      }));
      out.push(t("result.evidence.mu", {
        mu: Number(fp.mu_mas_yr || 0).toFixed(0),
        px: Number(fp.mu_px_yr || 0).toFixed(2),
      }));
    } else {
      out.push(t("result.evidence.consistentZero"));
    }
    if (Number(fp.amp_mag || 0) > 0.1) {
      out.push(t("result.evidence.amp", { amp: Number(fp.amp_mag).toFixed(2) }));
    }
    if (fp.period_d) {
      out.push(t("result.evidence.period", { period: Number(fp.period_d).toFixed(1) }));
    }
    out.push(t("result.evidence.epochs", {
      n: Number(fp.n_epochs || r.detected_epochs || 0),
      days: Number(fp.baseline_days || r.baseline_days || 0).toFixed(0),
    }));
    return out;
  }

  return {
    SUPPORTED_LANGS,
    DEFAULT_LANG,
    STORAGE_KEY,
    STRINGS,
    MESSAGES,
    get lang() { return currentLang; },
    init,
    setLang,
    resolveLang,
    onChange,
    t,
    apply,
    hasKey,
    serverText,
    serverList,
    errorText,
    catalogLabel,
    catalogReason,
    qualityText,
    qualityFlag,
    modeNote,
    resultView,
    buildEvidence,
  };
});
