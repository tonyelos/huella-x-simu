# View UI localization — English / Spanish (i18n)

> Scope: `系统/视图工具/**` only. App, database and MoveScope are not modified.
> Branch: `feat/view-i18n` (base `develop` at `5675dbb`).

## 1. Contract

- **English is the default and the primary language.** The page ships as `<html lang="en">` with English
  static text, so a reader without JavaScript still gets English.
- The header contains an **EN / ES** selector (`#lang-switch`, buttons `data-lang="en|es"`). The active button
  is exposed through `aria-pressed`.
- Initial language resolution: `?lang=en|es` in `location.search` → `lang` in `location.hash` →
  `localStorage['spherex.view.lang']` → **English**.
- Selecting a language stores it in `localStorage` and writes `lang` into the View URL via
  `history.replaceState`. A `?lang=` parameter overrides the stored value only for that visit.
- Switching language updates `<html lang>`, `document.title`, `meta description`, static text, aria labels,
  titles, placeholders, toasts, errors, target display names and all dynamic readouts. It does **not** change
  the selected region, data epoch, playback state, analysis window or handoff parameters.
- All UI strings live in `js/i18n.js` (`DICTS.en`, `DICTS.es`, `TARGET_NAMES.en/es`). Every key exists in both
  languages; values contain no Chinese characters.
- `lang` is a View-only URL parameter. The MoveScope handoff is unchanged:
  `ra/dec/fov/t0/t1/from=view` (no `lang`, no `target`, no `mode`).

## 2. String inventory (excerpt)

| Area | Key | English | Español |
|---|---|---|---|
| RA / Dec / FOV | `field.ra`, `field.dec`, `field.fov` | RA, DEC, FOV | RA, DEC, FOV |
| FOV unit | `unit.deg` | deg | grados |
| Field of view | `kv.fov` | Field of view | Campo de visión |
| Time window | `window.title` | Analysis time window | Ventana de tiempo de análisis |
| Window caveat | `window.note` | Window = **analysis-layer filter range**; not the data epoch / current time. | Ventana = **rango de filtrado de la capa de análisis**; no es la época de datos ni el instante actual. |
| UTC input | `time.utc.aria` | UTC time (YYYY-MM-DD HH:MM) | Hora UTC (YYYY-MM-DD HH:MM) |
| UTC error | `utc.invalid` | UTC time must use YYYY-MM-DD HH:MM (UTC). | La hora UTC debe usar el formato YYYY-MM-DD HH:MM (UTC). |
| Timeline | `time.axis` | UTC timeline | Línea de tiempo UTC |
| Playback label | `time.playnote` | Playback = continuous time | Reproducción = tiempo continuo |
| Play / pause | `play.aria`, `pause.aria` | Play, Pause | Reproducir, Pausar |
| Speed | `speed` | Speed | Velocidad |
| Selected region | `panel.right.title` | Selected region | Región seleccionada |
| Site / LST | `kv.site`, `kv.lst` | Zenith site, Local sidereal time | Sitio cenital, Tiempo sidéreo local |
| Coverage | `hits.head` | Local hits **2.47°** | Coincidencias locales **2.47°** |
| Coverage counts | `hits.count` | {frames} frames · {exposures} exposures · {nights} nights | {frames} fotogramas · {exposures} exposiciones · {nights} noches |
| Handoff | `open.movescope` | Open in MoveScope | Abrir en MoveScope |
| Copy | `copy.json`, `copy.link` | Copy JSON, Copy link | Copiar JSON, Copiar enlace |
| Handoff hint | `movescope.target` | Opening the **{name}** region; MoveScope analyzes by coordinates. You can switch modes after entering. | Se abrirá la región **{name}**; MoveScope analiza por coordenadas. Puedes cambiar de modo al entrar. |
| Handoff error | `movescope.unreachable` | Cannot reach {origin}: start MoveScope (app/server.py) first. | No se puede conectar con {origin}: inicia MoveScope (app/server.py) primero. |
| Data note | `data.note` | Data: data/fields.json (frames table via tools/build_field_index.py) · {frames} frames / {exposures} exposures. | Datos: data/fields.json (tabla frames vía tools/build_field_index.py) · {frames} fotogramas / {exposures} exposiciones. |
| Loader / fatal | `loader`, `fatal.load`, `fatal.webgl` | Loading globe and exposure index… / Failed to load… / WebGL2 unsupported… | Cargando el globo y el índice de exposiciones… / Error al cargar… / WebGL2 no compatible… |

## 3. Analysis modes and caveat semantics

Both languages keep the same meaning:

| Concept | English | Español |
|---|---|---|
| One handoff | One handoff: in MoveScope, switch between **Normal / Cascade-residual / Track**; t0–t1 is the analysis-layer filter range, not the data epoch. | Un solo traspaso: al entrar en MoveScope, cambia entre **Normal / Cascada-residual / Trayectoria**; t0–t1 es el rango de filtrado de la capa de análisis, no la época de datos. |
| Zero local hits | No local hits within 2.47°. **This does not mean it was never observed:** the local index is only a subset of 23 observing weeks (5,893 frames / 5,562 exposures). Try another coverage strip or click "Now" for a different epoch. | Sin coincidencias locales dentro de 2.47°. **Esto no significa que nunca se haya observado:** el índice local es solo un subconjunto de 23 semanas de observación (5,893 fotogramas / 5,562 exposiciones). Prueba otra franja de cobertura o haz clic en «Ahora» para ver otra época. |

The local index is never described as complete, and the UI makes no novelty or first-detection claims in either language.

## 4. Target display names

Coordinates and truth data remain in `data/targets.json`. Only the human-readable labels are localized:

| id | English | Español |
|---|---|---|
| `barnard` | Barnard's Star | Estrella de Barnard |
| `epsind_b` | Epsilon Indi B | Epsilon Indi B |
| `wise0855` | WISE 0855−0714 | WISE 0855−0714 |
| `rw_aur` | RW Aurigae | RW Aurigae |
| `r_dra` | R Draconis | R Draconis |
| `m101` | M101 | M101 |
| `campo_a` | Field A (DR1) | Campo A (DR1) |

## 5. Verification

### 5.1 Static smoke (no network)

```bash
python3 tools/smoke.py
```

Result: **65 PASS / 0 FAIL / 0 WARN**, exit code 0; `node --check` **5/5** (`main.js`, `globe.js`,
`skymap.js`, `astro.js`, `i18n.js`). The i18n checks cover: English default, header EN/ES selector, `?lang`
search→hash precedence, `localStorage` read/write, dictionary key parity (83 keys), no Chinese in UI
strings, interpolation placeholder parity, every `data-i18n` / `tr()` key present in both languages, exact
core labels, 7 localized target names, caveat semantics in both languages, `lang` excluded from the MoveScope
handoff, and the README/doc language assertions.

### 5.2 Browser acceptance (Ego)

Viewport 1470 × 714; page served locally from `系统/视图工具` on port 8930; `window.__errs` stayed empty.

- **English default:** `?ra=269.452077&dec=4.693365&fov=0.5&t=61000` → `<html lang="en">`, title
  `SPHEREx Sky View · MoveScope`, EN active, chips `5,893 frames / 5,562 exposures`, panel titles
  `Sky region selector` / `Selected region`, `UTC timeline`, `Playback = continuous time`, `Analysis time
  window`, `Local hits 2.47°`, `Copy JSON` / `Copy link` / `Open in MoveScope`, five UTC ticks and
  `0.15× / 1× / 4×`.
- **Switch to Spanish:** same region/time (`ra=269.452077`, `dec=4.693365`, `fov=0.5`,
  `t=61000.0000`, `t0=60970.0000`, `t1=61030.0000`); title becomes `SPHEREx Vista del cielo · MoveScope`,
  chips `5,893 fotogramas / 5,562 exposiciones`, panels `Selector de región celeste` / `Región seleccionada`,
  timeline `Línea de tiempo UTC`, playback `Reproducción = tiempo continuo`, window `Ventana de tiempo de
  análisis`, actions `Copiar JSON` / `Copiar enlace` / `Abrir en MoveScope`, target
  `Estrella de Barnard`, FOV unit `grados`. URL gains `lang=es`; `localStorage['spherex.view.lang'] = 'es'`.
- **Persistence / override:** reloading the clean URL keeps Spanish from `localStorage`; `?lang=en` overrides
  it and shows English again. No reload is needed for the header switch.
- **Handoff (Spanish UI):** clicking `Abrir en MoveScope` produces exactly

  ```text
  http://127.0.0.1:8790/?ra=269.452077&dec=4.693365&fov=0.5&t0=60970.0000&t1=61030.0000&from=view
  ```

  with no `lang`, `target` or `mode`; the View URL still contains `lang=es`.
- **Errors / notes:** unreachable MoveScope shows
  `No se puede conectar con http://127.0.0.1:8790: inicia MoveScope (app/server.py) primero.`
  Invalid UTC shows `La hora UTC debe usar el formato YYYY-MM-DD HH:MM (UTC).` An empty region shows
  `Sin coincidencias locales dentro de 2.47°. Esto no significa que nunca se haya observado: ...`
  Switching EN/ES clears a stale toast rather than leaving the old language on screen.
- **Playback:** the play button's aria label follows the language (`Reproducir` → `Pausar` → `Reproducir`);
  five UTC ticks and the speed buttons remain unchanged; `document.body.innerText` contains no Chinese in
  either language.

Screenshots:

- [English UI](i18n-en.png) — also used as [preview.png](preview.png).
- [Spanish UI](i18n-es.png).
