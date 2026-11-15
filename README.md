# MoveScope · 星动仪

**A multi-epoch SPHEREx image-analysis tool for motion and variability.**
MoveScope takes a sky position, retrieves several SPHEREx level-2 spectral
images, measures the position and flux of a target (and of the objects around
it), fits a physically motivated model family, and reports a **motion
fingerprint**, a classification, confidence, falsifiable evidence, and the
limits of what the data can support.

**English** | [中文](README.zh-CN.md)

> Design comes before code. Read the theory note first:
> [`系统/docs/01_理论设计编排.md`](系统/docs/01_理论设计编排.md).
> Implementation details, measured values, and known discrepancies are in
> [`系统/docs/02_实现与实测记录.md`](系统/docs/02_实现与实测记录.md).

![MoveScope UI](系统/docs/img/ui-main.png)

*Real data: Epsilon Indi B, eight SPHEREx epochs over a 361-day baseline. The
trajectory inset shows the measured 4.41″ displacement.*

---

## 1. What the tool does

MoveScope is a candidate-inspection tool, not a discovery engine. For a target
it reports one of the following motion classes:

| Class | Meaning | Typical interpretation |
|---|---|---|
| **L** | Linear proper motion | Nearby star / brown dwarf with measurable μ |
| **V** | Variable | Position stable, flux changes (variable star, AGN) |
| **A** | Accelerating / curved | Solar-system object or binary-orbit candidate |
| **S** | Stationary | Background star / galaxy; gives a 3σ motion upper limit |
| **U** | Undetermined | Readiness or data quality is insufficient; no guess |

Each result includes:

- **readiness** L0–L3 (1 / 2 / 3–5 / ≥6 epochs with a ≥6-month baseline);
- **model selection** over M0 stationary, M1 variable, M2 linear proper
  motion, M3 parallax, M4 proper motion + acceleration;
- **evidence chain** and **falsifier**;
- **provenance** REAL / HYBRID / SIM / SAMPLE; simulated data is never
  presented as real;
- an explicit `discovery_claim: false` boundary.

The tool deliberately speaks in terms of candidates and evidence. “No catalog
match” means “uncatalogued candidate”, never “new star” or “new planet”.

---

## 2. Current status

The app-side test suite currently has **286 tests**:

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
cd 系统/app
$PY -m unittest discover -s tests
# Ran 286 tests ... OK
```

Recent capabilities:

- main-canvas modes: **Browse / Cascade-residual / Tracks**;
- `t0/t1` time-window filtering for analysis and layer products;
- A-source and C-source batch catalog identification;
- CSV/JSON audit export, including candidate-expanded CSV;
- Gaia + 2MASS + WISE multi-catalog batch consumption with 0.5″ position
  deduplication;
- catalog-prior motion annotation (Barnard-like short-baseline case);
- a unified A + C + Tracks audit directory;
- A/C/Track candidate inspection without writing to the catalog database.

The five official benchmark targets in the current cache resolve to:

| Target | Expected | App result | Notes |
|---|---|---|---|
| Barnard's Star | L | **L (catalog prior)** | SPHEREx did not independently measure the motion; Gaia PM prior used |
| Epsilon Indi B | L + O | **L**, identity ambiguous | RUWE high; no forced single-catalog identity |
| WISE 0855 | L | **U** | data-limited; no forced L |
| RW Aurigae | V | **V** | flux variability, position stable |
| R Draconis | V | **V** | flux variability, position stable |

Result: **4 PASS + 1 DATA_LIMITED**. The benchmark is deliberately allowed to
fail or be data-limited; the tool does not tune labels to the expected answer.

---

## 3. Quick start

### Prerequisites

- Python 3.12 with NumPy and Pillow (the bundled runtime works out of the box);
- a browser with Canvas support;
- for the optional WebGL view: WebGL2;
- ports are local-only by default.

### One command (DB + app + view)

```bash
cd /Users/yiao/Documents/huellaX
项目主控/启动/start_all.sh
# open http://127.0.0.1:8790/
```

The launcher performs health checks and refuses to silently replace services
that are already running. See
[`项目主控/启动/README.md`](项目主控/启动/README.md).

### Manual start

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

# 1) main app
$PY 系统/app/server.py --port 8790

# 2) optional: SQLite/Blob data service (needed for arbitrary-coordinate DB lookup)
cd 系统/数据库工具
$PY cli.py serve --port 8797
```

Optional companion services:

```bash
# B: catalog / identification service (8799)
cd 系统/数据库工具
$PY tools/import_gaia.py --rebuild --offline   # offline fixture/bootstrap
$PY catalog_server.py --port 8799

# C: local coverage / discovery / fetch skeleton (8798)
cd 系统/anyfield
./start.sh 8798

# View: three.js sky/earth picker (8830)
cd 系统/视图工具
./start.sh 8830
```

The main page is <http://127.0.0.1:8790/>. If B or C is not running, the app
degrades explicitly; it does not invent catalog or coverage results.

### Basic URLs

```text
http://127.0.0.1:8790/                                      # Browse mode
http://127.0.0.1:8790/?target=barnard                      # preset
http://127.0.0.1:8790/?ra=269.452077&dec=4.693365&fov=0.2  # custom coordinates
http://127.0.0.1:8790/?target=barnard&mode=cascade          # main canvas overlay
http://127.0.0.1:8790/?target=barnard&mode=tracks
http://127.0.0.1:8790/?ra=269.452077&dec=4.693365&fov=0.5&t0=60890&t1=60950&from=view
```

`mode` is the canvas mode (`browse`, `cascade`, `tracks`), not the data mode.
The data mode is selected in the left panel (`auto`, `real`, `synthetic`,
`sample`). `/cascade` and `/tracks` remain available as detailed diagnostic
tables; the main page does not redirect to them when switching modes.

---

## 4. Architecture

```text
                         ┌─────────────────────────────┐
                         │ View  (optional, 8830)      │
                         │ sky picker / earth context  │
                         │ ra / dec / fov / t0 / t1    │
                         └──────────────┬──────────────┘
                                        │ one handoff URL
                                        ▼
┌──────────────────────────────────────────────────────────────────┐
│ MoveScope app (8790)                                             │
│                                                                  │
│  Browser UI (no build step)                                      │
│    Browse  ── analysis, source picking, lock, identify           │
│    Cascade ── L1 / masked / residual overlay                     │
│    Tracks  ── tracklet lines/nodes and D0–D4 levels              │
│                                                                  │
│  /api/analyze   → build_dataset → pipeline → result              │
│  /api/cascade   → per-epoch L1 / mask / residual                  │
│  /api/tracks    → tracklets → D0–D4                              │
│  /api/identify  → B catalog match (single source)                │
│  /api/identify/batch → B batch match (A/C sources)               │
│  /api/identify/batch/export → CSV/JSON attachment                 │
└───────────────┬───────────────────────┬──────────────────────────┘
                │                       │
                ▼                       ▼
┌───────────────────────────┐   ┌──────────────────────────────────┐
│ DB service (8797)         │   │ B catalog service (8799)         │
│ SQLite metadata + Blob    │   │ Gaia DR3 + derived 2MASS/WISE     │
│ epoch/coverage index      │   │ single/batch match, optional TAP   │
└───────────────────────────┘   └──────────────────────────────────┘
                │
                ▼
┌───────────────────────────┐
│ C coverage service (8798) │
│ local ingest / discovery  │
│ S3/IRSA cutout fetch      │
└───────────────────────────┘
```

The main app has no build step: static `web/` assets are served directly.
The analysis pipeline is pure Python + NumPy + Pillow; no Astropy/SciPy/FastAPI
is required for the core path.

### Analysis pipeline

```text
FITS / level-2 image
  → background + FLAGS mask + noise model
  → matched-filter detection
  → PSF / bloom centroid + aperture photometry
  → reference-star relative astrometric calibration
  → associate source positions across epochs
  → fit M0/M1/M2/M3/M4 model family
  → BIC model choice + 3σ / readiness gates
  → result, evidence, falsifier, provenance
```

Key implementation modules:

- `系统/app/core/photometry.py` — mask, matched filter, centroid, photometry;
- `系统/app/core/motion.py` — model family, BIC, motion fingerprint;
- `系统/app/core/pipeline.py` — detection → calibration → association → inference;
- `系统/app/core/catalog.py` — B catalog client, status mapping, multi-catalog
  position deduplication, optional ingest;
- `系统/app/core/catalog_prior.py` — generic Gaia-PM prior for an
  under-significant short-baseline S result;
- `系统/app/core/tracks.py` — cross-epoch tracklets and D0–D4 candidate levels.

### Time window

`/api/analyze`, `/api/cascade`, and `/api/tracks` accept `t0` / `t1`
(also `mjd_min` / `mjd_max`). The filter is applied after
`data_layer.build_dataset`:

- 0 epochs → HTTP 200 with an explicit empty-window result, never a fabricated
  measurement;
- 1 epoch → normal single-epoch result; tracks are marked `insufficient`;
- no `t0/t1` → old behavior unchanged.

The response includes `time_filter: {t0,t1,requested,kept,filtered}` and the UI
shows “from view / time window / kept N of M”.

---

## 5. Main interface modes

### Browse

The original behavior: multi-epoch blinking, stack, difference, target
trajectory, A-source table, lock/identify, readiness and model evidence.

### Cascade / residual

For each epoch:

- **L1**: 6σ detection, at most 80 objects per epoch;
- **masked**: the L1 positions, 2 px mask, reason `already_detected`;
- **residual**: 5σ re-detection after local-median masking, at most 80
  candidates per epoch, excluding duplicates within 2 px of L1.

The main canvas overlays L1 as solid cyan dots, masked as grey crosses, and
residuals as hollow amber circles. Layer toggles and per-frame counts are
available. Clicking a residual node locks it and calls `/api/identify`; it does
not re-run the whole analysis. Residual candidates are explicitly **not new
objects**.

Detailed diagnostic page: [`/cascade`](系统/app/web/cascade.html).

### Tracks

L1 + residual nodes are associated across epochs (greedy nearest-neighbor,
3 px gate, one-epoch gap allowed, no node reuse). Track models are
`stationary / linear / curved / variable / transient`, and candidate levels are:

- D0 artifact;
- D1 known catalog;
- D2 not implemented;
- D3 unknown moving;
- D4 unknown stationary;
- D5 out of scope (manual/follow-up).

The main canvas draws track lines and nodes; clicking a node jumps to its epoch
and invokes `/api/identify`. Track candidates are **not new objects**; four
epochs make the association inherently ambiguous and every track carries that
caveat.

Detailed diagnostic page: [`/tracks`](系统/app/web/tracks.html).

### A / C batch identification

- A table: analysis `sources`, paginated/batched at 200 per request;
- C table: `/api/sources` rows, batched at 200 per request;
- each source is sent to `/api/identify/batch`;
- statuses: known / ambiguous / no_match (`uncatalogued_candidate`) /
  insufficient / catalog unavailable / unknown;
- the A and C batch notes show counts and the requested catalogs;
- batch results are audit-exportable without changing the table.

### Unified candidate directory

A + C + Tracks are merged into one read-only audit table:

- A/C rows are deduplicated by `(mjd, round(ra,6), round(dec,6))`;
- same-position A/C rows merge `origins` into `A;C`;
- conflicting identities become `conflict`, never silently resolved;
- one track is one row; track nodes are **not** counted as new sources;
- counts: rows / unique_sources / tracks / known / ambiguous / no_match /
  insufficient / unknown / conflict;
- CSV and JSON export; UI shows the first 80 rows and counts over the full set.

---

## 6. Catalog identification and multi-catalog rules

### Status vocabulary

| Status | Meaning |
|---|---|
| `known_star` | A single catalog candidate with acceptable astrometric quality |
| `ambiguous` | Multiple physical candidates, or a quality/RUWE/PM conflict |
| `uncatalogued_candidate` | Catalog query succeeded with zero candidates; **not** “new object” |
| `insufficient_data` | Not enough information; e.g. a single candidate farther than 6.15″ |
| `catalog_unavailable` | Catalog service/coverage unavailable; **not** “no match” |

A single candidate farther than **6.15″** (one SPHEREx pixel) from the target is
rejected as an accidental alignment and returned as `insufficient_data` with
reason `CATALOG_OFFSET_TOO_LARGE`.

### Catalog-prior motion

For a short-baseline `class="S"` result, the app may apply a generic catalog
prior when:

- there is a single/ambiguous Gaia PM;
- the catalog PM is significant and > 1000 mas/yr;
- the measured and catalog PM agree within 3σ.

Then the class becomes `L` with:

- `catalog_motion.basis = "catalog_prior"`;
- `quality += CATALOG_PRIOR_MOTION, MEASUREMENT_NOT_SIGNIFICANT`;
- confidence ≤ 0.6;
- explicit text: “SPHEREx did not independently measure it; Gaia PM prior
  used; not a discovery”.

The measured μ, its error, `moving=false`, and the original significance are
preserved. The prior is never written as a measured proper motion.

### Multi-catalog batch

A/C batch requests use:

```json
{"catalogs": ["gaia", "2mass", "wise"]}
```

2MASS/WISE are **derived cross-match subsets of the Gaia cache**, not complete
surveys. The B service may report `catalog_status=partial`. The client:

- groups candidates from different catalogs within **0.5″**;
- prefers Gaia as the primary candidate, then 2MASS, then WISE;
- keeps other-catalog rows under `crossmatches`;
- does not merge two different positions; two physical groups remain
  ambiguous;
- does not treat multiple catalog rows for one physical source as multiple
  sources.

B failure remains `catalog_unavailable`; old B versions can fall back to Gaia
with an explicit `catalog_fallback="gaia"` marker.

---

## 7. API overview

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/analyze` | full motion analysis; accepts target/ra/dec/fov/mode/t0/t1 |
| `GET` | `/api/targets` | preset targets |
| `GET` | `/api/health` | app health |
| `GET` | `/api/sources` | C source-list proxy |
| `GET` | `/api/field` | C coverage handshake |
| `GET` | `/api/cascade` | per-epoch L1 / masked / residual |
| `GET` | `/api/tracks` | cross-epoch tracklets and D0–D4 |
| `POST` | `/api/identify` | single-source catalog identification |
| `POST` | `/api/identify/batch` | batch catalog identification (≤200 sources) |
| `POST` | `/api/identify/batch/export` | CSV/JSON audit attachment |

Examples:

```bash
curl -X POST http://127.0.0.1:8790/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"target_id":"barnard","fov":0.2,"mode":"auto"}'

curl 'http://127.0.0.1:8790/api/cascade?target_id=barnard&fov=0.2'
curl 'http://127.0.0.1:8790/api/tracks?target_id=barnard&fov=0.2'

curl -X POST http://127.0.0.1:8790/api/identify \
  -H 'Content-Type: application/json' \
  -d '{"ra":269.452077,"dec":4.693365,"mjd":60916.656,"sigma_arcsec":0.5}'
```

Batch endpoints are explicitly capped at 200 sources per request. The export
endpoint chunks A-sized inputs (215 → 200 + 15) and returns a CSV/JSON
attachment; JSON contains `sources`, `results`, and `byId`, and CSV expands
physical candidates into candidate rows.

Catalog auto-ingest is **off by default**:

```bash
export CATALOG_AUTO_INGEST=1
export MOVESCOPE_CATALOG_INGEST_TIMEOUT=60
```

When enabled, a partial/no-cached-field match may call B
`POST /api/v1/catalog/ingest` once and retry once. Ingest writes are B-side;
the app does not write the production DB. TAP availability remains an upstream
dependency.

---

## 8. Data modes

| Mode | Source | Purpose | Badge |
|---|---|---|---|
| REAL | Multi-epoch SPHEREx level-2 cutouts from IRSA/static cache | Real observations | green |
| HYBRID | Real single-epoch background/PSF/noise + injected target | When real epochs are insufficient | amber |
| SIM | Synthetic time series from real PSF/background/noise | Algorithm validation / teaching | blue |
| SAMPLE | Two bundled offline cutouts from different fields | Parsing/rendering only | grey |

The data path does not require TAP/SIA/DataLink for the known preset fields:
it uses the S3 weekly pointing index, HTTP Range headers, and static cutouts.
Arbitrary-coordinate coverage still depends on the local DB/C index and is
reported as `NO_LOCAL_COVERAGE` or `DB_UNAVAILABLE` when absent.

---

## 9. Validation and tests

### App tests

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
cd 系统/app
$PY -m unittest discover -s tests -v
# Ran 286 tests ... OK
```

Coverage includes: sample/real modes, FOV validation, caching and LRU,
target-coordinate conflicts, schema contracts, catalog status mapping,
catalog-prior motion, time filters, main-canvas modes, cascade layers,
tracklets, A/C batch identification, candidates/export, and unified-directory
deduplication.

### Benchmark and validation reports

- [`项目主控/验收/官方案例基准_report.md`](项目主控/验收/官方案例基准_report.md)
- [`项目主控/验收/官方案例基准_fullcache.md`](项目主控/验收/官方案例基准_fullcache.md)
- [`项目主控/验收/星表识别覆盖率.md`](项目主控/验收/星表识别覆盖率.md)
- [`项目主控/验收/GUI链路测试.md`](项目主控/验收/GUI链路测试.md)
- [`系统/docs/validation_report.json`](系统/docs/validation_report.json)
- [`系统/docs/motion_vs_variability.json`](系统/docs/motion_vs_variability.json)

Representative validation results:

- injection-recovery: 5 official targets × 8 random seeds = 40/40 correct;
- counterexamples: RW Aur and R Dra classified V across seeds; position slope
  compatible with zero;
- Epsilon Indi B: 8 real epochs, 361-day baseline, μ ≈ 4.71 ± 0.55″/yr,
  PA ≈ 129.6° (SIMBAD values are close);
- Barnard: short-baseline SPHEREx μ is not significant; reported as L with an
  explicit Gaia-PM prior, not as a measured SPHEREx detection;
- WISE 0855: data-limited U; no forced classification.

A/C batch and directory counts are data-dependent and are printed by the UI;
the E2E log in `系统/docs/02_实现与实测记录.md` is the authoritative record for
the current cache.

---

## 10. Repository layout

```text
系统/
  app/                     main MoveScope app (server + core + web)
    server.py              HTTP API, static UI, image rendering
    core/                  FITS, WCS, photometry, motion, pipeline, catalog,
                           tracks, catalog prior, time/source utilities
    web/                   no-build browser UI and diagnostic pages
    tests/                 app unit/integration tests
  docs/
    01_理论设计编排.md       theory baseline (read first)
    02_实现与实测记录.md     implementation, measurements, discrepancies
    validation_report.json  injection-recovery
  anyfield/                C coverage/discovery/fetch service
  数据库工具/               DB + B catalog service
  视图工具/                 optional three.js sky/earth view
  tools/                   week-index/epoch search/validation utilities
  参考 资料/               third-party references and notes

项目主控/
  架构/                    architecture and evolution notes
  验收/                    benchmark/acceptance reports
  启动/                    start/stop launcher
  README.md                project-control notes
```

Key documents:

- [Theory design](系统/docs/01_理论设计编排.md)
- [Implementation & measured log](系统/docs/02_实现与实测记录.md)
- [Mode refactor plan](项目主控/架构/14_模式化重构计划.md)
- [Catalog ingest plan](项目主控/架构/17_数据接收接口与自动缓存.md)
- [Batch identification plan](项目主控/架构/18_批量星表识别.md)
- [Audit/export plan](项目主控/架构/20_批量结果审计导出.md)
- [Unified candidate directory plan](项目主控/架构/22_统一候选目录.md)

---

## 11. Configuration and environment

| Variable | Default | Purpose |
|---|---|---|
| `CATALOG_URL` / `MOVESCOPE_CATALOG_URL` | `http://127.0.0.1:8799` | B catalog base URL |
| `MOVESCOPE_CATALOG_TIMEOUT` | `2.5` s | B match timeout |
| `CATALOG_AUTO_INGEST` | off | Allow one ingest+retry on partial/no-cached-field |
| `MOVESCOPE_CATALOG_INGEST_TIMEOUT` | `60` s | B ingest timeout |
| `ANYFIELD_URL` | `http://127.0.0.1:8798` | C coverage base URL |
| `ANYFIELD_TIMEOUT` | `1.5` s | C coverage timeout |
| `MOVESCOPE_DB_URL` | DB service default | DB endpoint override |

The app never silently switches to a different port, and the launcher refuses
to replace a live service with a mismatched PID.

---

## 12. Limitations and responsible use

1. **Not a discovery.** Every catalog status and every track is a candidate.
   `discovery_claim` is false; no “new planet/star” claim is made.
2. **Pixel scale.** SPHEREx is about 6.15″/pixel. Motion below ~0.5″/yr cannot
   be measured reliably in a short baseline; background stars and galaxies are
   often `S` with an upper limit.
3. **Saturation.** Barnard's Star is saturated in some SPHEREx data; bloom
   centroid errors are large and explicitly flagged (`SATURATED_SOURCE`).
4. **Catalog coverage.** B may be partial; 2MASS/WISE are derived cross-match
   subsets of the Gaia cache, not complete surveys. `partial` and
   `catalog_unavailable` are not “not found”.
5. **4-epoch ambiguity.** Cross-epoch association with only four epochs is
   underdetermined; D0–D4 are candidates, not confirmation.
6. **Residuals.** Cascade residuals can contain PSF wings, edge artifacts, and
   noise. A residual is not a new astrophysical source.
7. **TAP.** Real TAP ingest has been blocked by upstream timeouts in this
   environment. Auto-ingest is off by default; simulated TAP success is never
   reported as real.
8. **No full-sky service.** The local catalog cache covers a small set of
   preset fields; arbitrary fields require an explicit DB/C ingest path.

---

## 13. Citations and acknowledgements

If you use this repository, please cite the repository and the original data
papers. The full source/license inventory is in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and the short submission list
is in [`REFERENCES.md`](REFERENCES.md); machine-readable metadata is in
[`CITATION.cff`](CITATION.cff).

### Mission data

- **SPHEREx level-2 spectral images (QR3/DR1)**, NASA/IPAC Infrared Science
  Archive (IRSA), <https://irsa.ipac.caltech.edu/Missions/spherex.html>.
  SPHEREx is a NASA mission; see NASA mission pages and the IRSA data
  documentation for current release/version details.
- **NASA Space Apps Challenge 2026 — Planet X and SPHEREx**,
  <https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/>.

### Catalogs and name resolution

- **Gaia DR3**: Gaia Collaboration et al. (2023), *A&A* 674, A1,
  <https://doi.org/10.1051/0004-6361/202243940>.
- **2MASS**: Skrutskie et al. (2006), *AJ* 131, 1163,
  <https://doi.org/10.1086/498708>.
- **WISE / AllWISE**: Wright et al. (2010), *AJ* 140, 1868,
  <https://doi.org/10.1088/0004-6256/140/6/1868>; Cutri et al. (2021),
  AllWISE Source Catalog, VizieR II/328.
- **SIMBAD**: Wenger et al. (2000), *A&AS* 143, 9,
  <https://doi.org/10.1051/aas:2000332>.
- 2MASS/WISE derived fields in B use IRSA Gator cross-matches to Gaia IDs;
  coordinates/PM remain Gaia-based and are not a replacement for the original
  survey astrometry.

### Software and assets

- **three.js r160** (MIT), <https://threejs.org/>; license text in
  `系统/视图工具/vendor/three.LICENSE`. The optional 3D view uses three.js
  example patterns (rewritten, not wholesale copied).
- **Earth textures**: NASA Visible Earth / Blue Marble,
  <https://visibleearth.nasa.gov/collection/1484/blue-marble>; distributed via
  the three.js examples with MIT/NASA media terms.
- **Runtime**: Python 3.12 (PSF-2.0), NumPy (BSD-3-Clause), Pillow (HPND).
- UI/interaction inspiration: WiseView (Backyard Worlds: Planet 9),
  <http://byw.tools/wiseview>; the project does not reuse its code/data.

### License

Original code, documentation, figures, and screenshots are licensed under the
**Apache License 2.0**; see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
Third-party material remains under its own license; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`REFERENCES.md`](REFERENCES.md). NASA does not endorse this project.

---

## 14. Contributing and reproduction

- Keep changes reviewable and add tests for behavior changes.
- Do not write the production catalog DB from app tests.
- Do not turn partial/unavailable catalog states into “no match”.
- Do not add positive “new planet/new star” language to UI or exports.
- For validation, use the launcher and the current cached fields; record
  provenance and the exact test command.

A minimal reproduction:

```bash
PY=python3
cd 系统/app
$PY -m unittest discover -s tests
$PY server.py --port 8790
# open http://127.0.0.1:8790/
```

Welcome to open issues with reproducible coordinates, data provenance, and
expected/actual status.
