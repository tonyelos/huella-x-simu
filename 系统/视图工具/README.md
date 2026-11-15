# SPHEREx Sky View · MoveScope (View tool)

Turns the database's **5,893 frames / 5,562 SPHEREx exposures** into a rotating 3D globe and a sky-region
selector: click the globe to see which site has that sky region at its zenith, or frame a region on the
sky map and let the globe mark the sites where it is overhead. A local exposure index answers
"**did the local data cover this region, and how many frames/nights?**".

The UI is **English-primary with a complete Spanish translation**. The header **EN / ES** switch and
`?lang=en|es` select the language; the choice is remembered in `localStorage`. See §2.6.

![Preview](docs/preview.png)

---

## 1. Run

```bash
cd 系统/视图工具
./start.sh            # default: http://127.0.0.1:8830/
./start.sh 8831       # another port
```

- Fixed port **8830** (MoveScope 8790, database 8797); the script **does not scan ports** and exits with an error if the port is taken.
- The page uses ES modules and `fetch`, so it must be served over HTTP; opening `index.html` directly is blocked by the browser.
- No build step and no network dependency: three.js and the Earth textures are vendored in `vendor/` and `assets/`.
- WebGL2 is required. If it is unavailable the loader states that the browser does not support WebGL2 instead of asking you to start a server.

## 2. Deep-link contract (T1)

```text
http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&target=barnard
http://127.0.0.1:8830/?target=epsind_b          # target only, preset coordinates
http://127.0.0.1:8830/?ra=120.5&dec=12.25&fov=0.4&t=61300.2
http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&target=barnard&t=61000&t0=60970&t1=61030
http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&t=61000&lang=es
```

| Parameter | Meaning | Default |
|---|---|---|
| `ra` / `dec` | Region center (degrees, ICRS) | Barnard's Star |
| `fov` | Field of view (degrees, 0.05–180) | 3.5 (default) / target preset |
| `target` | Target id (`barnard`, `epsind_b`, `wise0855`, `rw_aur`, `r_dra`, `m101`, `campo_a`) | — |
| `t` | Data epoch / current instant (MJD) | last exposure in the local index |
| `t0` / `t1` | Analysis time-window bounds (MJD, current epoch ±N days) | `t ± 365` |
| `lang` | UI language: `en` or `es` | `en` (or the stored choice) |

- **Resolution order:** `location.search` first, `location.hash` as a fallback (old `#ra=…` links still work). The same order applies to `lang`.
- Invalid or missing values fall back to defaults; nothing throws.
- Page state is written back to **search** with `history.replaceState` (including `target`, `t`, `t0/t1` and `lang`), so "Copy link" reproduces the view.
- `t0/t1` are the **analysis-layer filter range**, not the data epoch `t`; see §3.5.

## 2.5 Time selection and continuous playback

Every time entry point uses **`commitTime(mjd, mode, {animate, source})`**, which synchronizes `state.mjd`,
the clock/MJD, the slider, the UTC input, the analysis window `t0/t1`, the URL and the Earth rotation:

- **UTC input:** `YYYY-MM-DD HH:MM` plus **Go**; invalid input shows a toast instead of failing silently. The window is recomputed with the current `windowDays`.
- **Step buttons:** `−1d / −1h / +1h / +1d`.
- **Slider:** kept; dragging updates live and the URL is still throttled at 300 ms.
- **Timeline:** 5 UTC date ticks (`t-start / t-25 / t-mid / t-75 / t-end`) labelled **UTC timeline**; the current instant is shown by the clock and the slider position.
- **Playback:** continuous time, `mjd += dt × speed`. The playback loop only calls `commitTime(..., {silent:true, animate:false, source:'play'})`.
  - Speeds: `0.15× = 0.15 days/s`, `1× = 0.6 days/s`, `4× = 2.4 days/s`; switching takes effect immediately.
  - At `mjdMax` playback wraps around to `mjdMin`; starting from the end/now first returns to `mjdMin` (legacy behaviour).
  - No Earth tween while playing; the Earth angle follows time continuously, and after pause it matches `state.mjd` exactly.
  - The UI states **Playback = continuous time**.
- **Earth tween:** only when time is changed manually (UTC/go, slider, ±, keyboard, hit row, curated epoch): 700 ms, shortest path, ease-out; no animation at boot.
- Screenshots: [UTC input](docs/time-input.png) · [playing](docs/time-playing.png) · [timeline](docs/time-axis.png).

## 2.6 Language / Idioma

The View UI is **English by default** and **fully translated into Spanish**. The header has an **EN / ES**
selector next to the clock.

- Resolution order for the initial language: `?lang=en|es` (search first, hash fallback) → `localStorage` → **English**.
- Persistence key: **`spherex.view.lang`**. A choice made with the header switch survives reloads; `?lang=` overrides the stored value for that visit.
- On switch, `<html lang>`, `document.title`, `meta description`, static labels, aria labels, titles, placeholders, toasts, errors and all dynamic readouts (clock, coverage, hits, hints, notes) update without reloading and **without changing the selected region, data epoch, playback state, analysis window or handoff parameters**.
- All UI strings, including both target display names such as **Field A (DR1) / Campo A (DR1)**, live in `js/i18n.js`. Both dictionaries carry the same keys; `tools/smoke.py` verifies key parity, non-empty values, no Chinese characters, and that every `data-i18n*` / `tr('…')` key used by the page exists in both languages.
- `lang` is a **View-only** URL parameter. The MoveScope handoff never includes it: the handoff stays `ra/dec/fov/t0/t1/from=view`.
- Caveat semantics are preserved in both languages: the local index is a subset of **23 observing weeks**, and "no local hits" **does not mean it was never observed**. See §4.
- Screenshots: [English UI](docs/i18n-en.png) · [Spanish UI](docs/i18n-es.png); full string inventory and browser evidence in [docs/i18n-en-es.md](docs/i18n-en-es.md).

## 3. Open in MoveScope (T2)

The **only analysis entry point** is "Open in MoveScope": one handoff opens

```text
http://127.0.0.1:8790/?ra=<current RA>&dec=<current Dec>&fov=<current FOV>&t0=<MJD>&t1=<MJD>&from=view
```

- Uses the current **full-precision** `ra/dec/fov`; no `target`, no `mode`, and no `lang`. MoveScope starts in its normal mode.
- `t0/t1` is the analysis-layer filter window (§3.5), not the current data epoch.
- After entering MoveScope the user switches **Normal / Cascade-residual / Track** on the same canvas; View exposes no separate cascade/track page buttons.
- **Health check:** `GET /api/health` (`mode:'no-cors'`). If MoveScope is not running, View copies the link and says to start `app/server.py`; it never pretends the page opened.
- **Origin:** `MOVESCOPE_ORIGIN` defaults to `http://127.0.0.1:8790`; it can be overridden with `window.MOVESCOPE_ORIGIN` before page load (no source edit needed for integration or port changes).
- **No preset match:** MoveScope still analyzes the current coordinates; it uses REAL when real epochs exist, otherwise SIM. View never fabricates a data mode.

Measured (2026-11-15 06:20, MoveScope :8790, handoff URL `ra=269.452077&dec=4.693365&fov=0.5&t0=60970&t1=61030&from=view`):
MoveScope badge **REAL**, **L2 readiness**, **4 epochs**. MoveScope appends `target=custom` to its own
address bar; the parameters emitted by View remain unchanged.

## 3.5 Analysis time window and one handoff

The bottom time bar has an **Analysis time window ±N days** control:

- Options **30 / 90 / 180 / 365 / 730**, default **365**.
- Always centered on the current `state.mjd`: `t0 = t − N`, `t1 = t + N`; dragging the timeline, playback and "Now" all recompute it.
- Shows **MJD t0–t1** and the **UTC** start/end; page state writes `t0/t1` back to the URL so refresh is reproducible.
- The UI states: **the window is the analysis-layer filter range, not the data epoch / current time**. `t` controls the region/playback position; `t0/t1` are delivered with the single handoff.
- View has exactly one analysis entry point; after entering MoveScope, switch **Normal / Cascade-residual / Track**. The old standalone analysis pages remain diagnostic pages on the MoveScope side but are not linked from View and have no hidden button here.

Example handoff (Barnard, ±30 days, t=61000):

```text
http://127.0.0.1:8790/?ra=269.452077&dec=4.693365&fov=0.5&t0=60970.0000&t1=61030.0000&from=view
```

- Before opening, View GETs `/api/health`; when unreachable it shows a clear toast and copies the link instead of failing silently.
- The MoveScope analysis page caps FOV at **3.0°**; when the current FOV is above 3°, View asks you to reduce it (the link still carries the current FOV, never a fake value).
- View delivers `ra/dec/fov/t0/t1/from=view`; MoveScope owns mode switching and window consumption. This tool does not modify the app.

## 4. "Local hits" definition (T5)

**2.47°** is the half-diagonal of a single 3.49° square field of view. A frame counts as covering the region
only when its center lies within 2.47° of the region center. Counts are split into three layers:

| Metric | Meaning |
|---|---|
| **Frames** | Matching detector frames (one row = one detector) |
| **Exposures** | Distinct `obsid` values (one obsid = one exposure, at most 6 frames) |
| **Nights** | Distinct UTC dates |

With this definition (within the local index):

| Target | Frames | Exposures | Nights | Nearest center distance |
|---|---|---|---|---|
| Barnard's Star | 10 | 5 | 3 | 13.7′ |
| Epsilon Indi B | 27 | 14 | 7 | 28.1′ |
| WISE 0855 | 26 | 12 | 6 | 56.3′ |
| RW Aurigae | 8 | 7 | 3 | 110.6′ |
| R Draconis | 11 | 8 | 6 | 49.3′ |
| M101 | 10 | 5 | 2 | 68.7′ |
| Field A | 9 | 5 | 4 | 0.1′ |

> Snapshot: 2026-11-15 00:10 CET, database `frames` 5,893 rows / 5,562 exposures / 23 observing weeks;
> `tools/smoke.py` re-checks this table against `fields.json` and the 2.47° definition.

- With zero hits the UI says "No local hits within 2.47°" and explains that **the local index is only a subset of 23 observing weeks** — **this does not mean it was never observed**.
- For a matched target, the panel also shows **curated epochs** (deduplicated exposures from `target_epochs`: Barnard 4, Eps Ind 10, RW Aur 1); clicking one jumps to that epoch.

## 5. Data sources and rebuild (T3/T4)

```text
系统/数据库工具/var/catalog.sqlite : frames        ──build_field_index.py──▶  系统/视图工具/data/fields.json
系统/app/data/targets.json                          ──sync_targets.py──────▶  系统/视图工具/data/targets.json
系统/数据库工具/var/exports/epoch_catalog.json      ──sync_targets.py──────▶  系统/视图工具/data/epoch_index.json
```

```bash
# Rebuild the exposure index (reads the database by default; refuses to overwrite without --force)
python3 tools/build_field_index.py --check     # compare only
python3 tools/build_field_index.py --force

# Rebuild from the DB export into a temp file (proves no header_cache / removed-directory dependency)
python3 tools/build_field_index.py --source export --out /tmp/fields.json --force

# Rebuild targets + curated epochs (single source: app/data/targets.json)
python3 tools/sync_targets.py --check
python3 tools/sync_targets.py
```

- `fields.json` no longer depends on the removed `app/data/header_cache`; `--source export|db|headers` is selectable and defaults to `db`.
- `--out` accepts absolute or relative paths (relative to the current working directory); the default is `data/fields.json`, and an existing file is not overwritten without `--force`.
- The snapshot follows the database: rebuilding tracks the `frames` table (currently 5,893 frames / 5,562 exposures). A failing `--check` means the source changed; rerun it.
- Output includes `generated_by / source / source_sha256 / generated_at / count / exposures`.
- `data/targets.json` is a generated copy (including `coord_unc_arcsec`; `rw_aur.truth` = 12.0 d / 0.85 mag); do not maintain a second manual copy.
- Rebuild results match the database row for row (`--check` exit code 0); `--source export` and `db` currently produce identical rows.

## 6. Self-check

```bash
python3 tools/smoke.py
```

One command checks: DOM ids; fields row count/source/database parity; targets/epoch_index single-source parity;
handoff contract (`MOVESCOPE_ORIGIN` default 8790 + GET + full-precision `ra/dec/fov` + `t0/t1` + `from=view`, no
`mode/target/lang`); analysis time window (30/90/180/365/730 + `t0/t1` centered on `t`); UTC input/step
buttons/5 UTC ticks; single `commitTime` (time and `globe.setClock` entry point); continuous playback
(0.15×/1×/4×, end wrap); `animate:false` during playback; 0.7 s tween for manual time changes; exactly one
analysis entry point; the "switch Normal/Cascade-residual/Track in MoveScope" copy; 2.47° coverage table
matching README (RW Aurigae non-zero); **EN/ES localization** (default English, header selector, `?lang=`,
localStorage key, key parity between both dictionaries, no Chinese characters in UI strings, caveat copy in
both languages, and `lang` absent from the handoff); `fields` rebuild from DB/export; no old project
name/port/stale copy; `node --check`; `start.sh` port. Exit code 0 means everything passed.

## 7. Boundaries

1. **No FITS rendering:** multi-epoch images/blinking/differencing belong to MoveScope; this tool only selects a region.
2. **The local index is a subset:** 5,893 frames cover only 23 observing weeks (2025-04-24 → 2026-10-03); gaps do not mean no observations.
3. **Custom regions are judged by MoveScope:** coordinates are analyzed with REAL when real epochs exist, otherwise SIM; View does not fabricate.
4. The hit criterion is "center distance ≤ 2.47°", not a per-pixel intersection; for steep scan strips this is a conservative estimate.
5. Earth textures are 2048 px; they look soft at 2× near zoom.
6. **The analysis window is consumed by MoveScope's mode bar:** View only computes and sends `t0/t1/from=view`; MoveScope decides how the window filters.
7. **MoveScope analysis FOV cap is 3.0°:** View allows a larger browsing FOV and warns to reduce it above 3°; the link still carries the real value.
8. **View exposes no separate cascade/track entry:** those diagnostic pages stay on the MoveScope side, with no link or hidden button here.
9. **Playback is continuous time:** `mjd += dt × speed`, speeds 0.15×/1×/4×; at the end it wraps to `mjdMin`; starting from the end/now first returns to `mjdMin`.
10. **Earth tween only on manual time changes:** playback uses `animate:false`; manual jumps settle in 0.7 s, then exactly match `state.mjd`.
11. **Language is presentation only:** switching EN/ES changes strings, `<html lang>`, title and aria labels, never `ra/dec/fov/t0/t1`, the data epoch, playback or the MoveScope handoff.

## 8. File structure

```text
系统/视图工具/
├── index.html              single page, English-default data-i18n markup
├── css/style.css           HUD styles + EN/ES switch
├── js/
│   ├── main.js             state, deep links, handoff, coverage, language switching
│   ├── globe.js            three.js globe
│   ├── skymap.js           sky-map selector
│   ├── astro.js            time / coordinates / Sun
│   └── i18n.js             EN/ES dictionaries, persistence, target display names
├── data/
│   ├── fields.json         5,893 frames / 5,562 exposures (generated)
│   ├── targets.json        7 targets (generated)
│   └── epoch_index.json    37 curated epochs (generated)
├── tools/
│   ├── build_field_index.py
│   ├── sync_targets.py
│   └── smoke.py
├── docs/                   screenshots, historical reports, i18n evidence
├── vendor/three.module.js
├── vendor/three.LICENSE    full three.js MIT text
├── assets/earth_*.{jpg,png}
└── start.sh
```

## 9. References and licensing

> Original code/documentation in this tool is **Apache-2.0**; third-party material is itemized in the root
> `THIRD_PARTY_NOTICES.md`. Only direct dependencies are listed here.

- **three.js r160** (MIT): `vendor/three.module.js` is the minified r160 ESM build (sha256 `3e690ac7…`), with the
  `SPDX-License-Identifier: MIT` header preserved; the full MIT text is in `vendor/three.LICENSE`. Source:
  <https://threejs.org/>, <https://github.com/mrdoob/three.js>.
- **Earth textures:** `assets/earth_atmos_2048.jpg`, `earth_specular_2048.jpg` match the three.js r160
  `examples/textures/planets/` files by sha256; `earth_clouds_1024.png`, `earth_lights_2048.png` match r160 pixel
  for pixel and were only re-encoded as PNG. They are distributed through the three.js repository (MIT); the
  original imagery is credited to **NASA Visible Earth / Blue Marble** (NASA media guidelines):
  <https://visibleearth.nasa.gov/collection/1484/blue-marble>,
  <https://www.nasa.gov/nasa-brand-center/images-and-media/>. NASA does not endorse this project.
- **SPHEREx data:** NASA/IPAC IRSA (QR3/DR1), <https://irsa.ipac.caltech.edu/Missions/spherex.html>; used only
  for the local index/cache and visualization.
- **Visual and interaction references:** [bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view)
  (Cesium + HUD); **no code or data reused** — this tool draws with three.js and runs offline. The interaction
  model also references WiseView (Backyard Worlds: Planet 9) <http://byw.tools/wiseview>.
- System font stack only; no third-party fonts downloaded or distributed. Icons are CSS/Unicode or hand-drawn SVG.
- NASA Space Apps official Legal/FAQ: <https://www.spaceappschallenge.org/legal/>,
  <https://www.spaceappschallenge.org/faq/>.
