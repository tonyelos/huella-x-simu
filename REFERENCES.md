# References, Licenses, and Attribution (Submission-Ready)

> Project: **MoveScope / 星动仪 (huella-x)**
> Challenge: **NASA Space Apps Challenge 2026 — Planet X and SPHEREx**
> Submission date: 2026-11-15
> Repository: <https://github.com/tonyelos/huella-x-simu>
>
> This file is the short, submission-form-ready citation list.
> The full component/file-level inventory is in
> [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md); the license texts are in
> [`LICENSE`](LICENSE), [`NOTICE`](NOTICE), and
> [`系统/视图工具/vendor/three.LICENSE`](系统/视图工具/vendor/three.LICENSE).

---

## 1. Original content license

**All original code, documentation, figures, and screenshots created for this
Challenge are licensed under the Apache License, Version 2.0.**

- License: Apache-2.0
- Full text: [`LICENSE`](LICENSE)
- Copyright/notice: [`NOTICE`](NOTICE)
- SPDX identifier: `Apache-2.0`

We explicitly select Apache-2.0. We do not rely on NASA's default distribution
license; the default is mentioned here only for transparency.

---

## 2. Third-party software / code

| Resource | Version | License | Source | Used in |
|---|---|---|---|---|
| three.js | r160 (minified ESM build) | **MIT** | <https://threejs.org/> · <https://github.com/mrdoob/three.js> | `系统/视图工具/vendor/three.module.js` |
| three.js examples (API/shader patterns) | r160 | **MIT** | <https://github.com/mrdoob/three.js/tree/r160/examples> | `系统/视图工具/js/globe.js`, `js/skymap.js` (rewritten, not copied) |

- three.js MIT text is included at
  [`系统/视图工具/vendor/three.LICENSE`](系统/视图工具/vendor/three.LICENSE).
- MIT is OSI-approved and compatible with Apache-2.0.

### Runtime dependencies (not vendored / not redistributed)

| Resource | License | Source |
|---|---|---|
| Python 3.12 | PSF License 2.0 | <https://www.python.org/> |
| NumPy 2.x | BSD-3-Clause | <https://numpy.org/> |
| Pillow 11.x | HPND | <https://python-pillow.org/> |

If a future package redistributes these runtimes, their license texts must be
included in the package.

---

## 3. Images / Earth textures

| Resource | License / terms | Source | Files |
|---|---|---|---|
| Earth day, specular, clouds, night-lights textures | three.js repository **MIT**; original imagery credited to **NASA Visible Earth / Blue Marble** under NASA media terms | <https://github.com/mrdoob/three.js/tree/r160/examples/textures/planets> · <https://visibleearth.nasa.gov/collection/1484/blue-marble> · <https://www.nasa.gov/nasa-brand-center/images-and-media/> | `系统/视图工具/assets/earth_atmos_2048.jpg`, `earth_specular_2048.jpg`, `earth_clouds_1024.png`, `earth_lights_2048.png` |

- `earth_atmos_2048.jpg` and `earth_specular_2048.jpg` are byte-identical to
  the three.js r160 originals.
- `earth_clouds_1024.png` and `earth_lights_2048.png` are pixel-identical to
  the three.js r160 originals; only PNG encoding differs.
- NASA does not endorse this project; NASA names/logos are not used to imply
  endorsement.
- Project screenshots (`系统/docs/img/*.png`,
  `系统/视图工具/docs/*.png`) are original compositions and are Apache-2.0,
  with the above texture/data attribution retained.

---

## 4. Data

| Resource | Terms | Citation / source | Used in |
|---|---|---|---|
| SPHEREx level2 spectral images (QR3 / DR1), static cutouts, S3 headers | NASA/IPAC IRSA public data; no NASA endorsement implied | NASA/IPAC Infrared Science Archive, *SPHEREx*, <https://irsa.ipac.caltech.edu/Missions/spherex.html>; S3 public bucket `nasa-irsa-spherex` | `系统/参考 资料/muestras/*.fits`, `系统/app/data/header_cache/*.hdr`, `epochcache/*.npz`, `系统/数据库工具/var/catalog.sqlite`, `var/blobs/*` |
| Target coordinates and reference values | SIMBAD/CDS; factual data; cite the database | Wenger, M., Ochsenbein, F., Egret, D., et al. 2000, *A&AS*, 143, 9, DOI: <https://doi.org/10.1051/aas:2000332>; CDS <https://cds.unistra.fr/> | `系统/app/data/targets.json` |
| Gaia DR3 star-catalog test fixture | **CC BY-NC 3.0 IGO** (non-OSI; non-commercial; attribution required) | ESA Gaia Archive, TAP <https://gea.esac.esa.int/tap-server/tap/sync>, <https://gea.esac.esa.int/archive/>; Gaia Collaboration et al. 2023, *A&A* 674, A1, DOI: <https://doi.org/10.1051/0004-6361/202243940>; license <https://www.cosmos.esa.int/web/gaia-users/license> | `系统/数据库工具/tests/fixtures/gaia_dr3_sample.csv` (**234 real Gaia rows, incomplete, tests only**); local `var/star_catalog.sqlite` (7 MB, gitignored, not distributed) |
| Derived indexes/catalogs | Original processing under Apache-2.0; underlying data remains NASA/CDS | `系统/app/data/week_index.json`, `epoch_catalog.json`, `targets.json`, `系统/视图工具/data/fields.json`, `系统/数据库工具/var/catalog.sqlite` | production DB / offline demo |

- We only index/cache/derive locally; we do not redistribute whole 65 MB
  observations.
- The Gaia fixture is a **234-row incomplete subset** of real Gaia DR3 TAP
  results for offline tests, not a full-sky or per-field complete catalog; the
  17,679-row local cache is not distributed.
- Data files are not relicensed as Apache-2.0; the Apache license applies only
  to original code/docs.

---

## 5. References / inspiration (no code or data reused)

| Resource | Use | Link |
|---|---|---|
| WiseView (Backyard Worlds: Planet 9) | interaction paradigm (blink/timeline/FOV) | <http://byw.tools/wiseview> |
| bilawalsidhu/gods-eye-view | visual reference for 3D Earth/HUD; no code/data reused | <https://github.com/bilawalsidhu/gods-eye-view> |
| CDS Aladin | visualization tool reference; not integrated | <https://aladin.cds.unistra.fr/> |
| Astropy | ecosystem reference; not used | <https://www.astropy.org/> |
| Meeus, *Astronomical Algorithms* (2nd ed.) | solar-position/ sidereal-time formulas (math facts; no code copied) | ISBN 978-0943396613 |
| NASA Space Apps Challenge 2026 challenge page | challenge requirements | <https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/> |
| The SPHEREx Satellite Mission (2026) | instrument facts: 102 bands, 0.75–5.0 µm, R 35–130, 6.2″ pixels | <https://doi.org/10.3847/1538-4357/ae2be2> |
| Spectral Map Making with SPHEREx (2026) | 2040×2040 arrays, LVF/PSF sampling | <https://doi.org/10.3847/1538-4365/ae91df> |

---

## 6. Official terms cited

- NASA Space Apps Legal:
  <https://www.spaceappschallenge.org/legal/>
  — requires all sources to be cited; third-party materials must be unrestricted
  or OSI-licensed; licenses must be identified in submitted documentation;
  original content must be open source, with Apache-2.0 as NASA's default.
- NASA Space Apps FAQ:
  <https://www.spaceappschallenge.org/faq/>
- OSI Open Source Definition: <https://opensource.org/docs/osd>
- Apache-2.0: <https://www.apache.org/licenses/LICENSE-2.0>
- MIT: <https://opensource.org/license/mit>
- NASA media guidelines:
  <https://www.nasa.gov/nasa-brand-center/images-and-media/>

---

## 7. Compliance statement

1. Every third-party code, data, image, and reference used in this project is
   listed above and in `THIRD_PARTY_NOTICES.md`; nothing is intentionally
   uncredited.
2. All third-party software licenses are OSI-approved (MIT, BSD-3-Clause,
   HPND, PSF-2.0); NASA data is public data. The **only non-OSI data exception**
   is the Gaia DR3 test fixture (CC BY-NC 3.0 IGO, non-commercial, attribution
   required), kept to 234 real rows for offline tests; no full Gaia catalog or
   local 17,679-row cache is distributed, and the project does not claim OSI
   status for Gaia data.
3. Original content is Apache-2.0; no NASA default license is relied upon.
4. No third-party confidential information, no export-controlled information,
   and no NASA endorsement claim.
5. Reference materials under `系统/参考 资料/` are third-party materials and
   are **not** relicensed under Apache-2.0; see
   [`系统/参考 资料/NOTICE.md`](系统/参考%20资料/NOTICE.md).
