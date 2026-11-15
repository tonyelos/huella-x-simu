# Third-Party Notices and Attributions

> 本文件是提交文档的一部分，逐项列出本项目使用的第三方代码、数据、图片、字体和参考资料，以及它们的许可证/使用条款与来源。
> 简版/可直接粘贴到提交表单的版本见 [`REFERENCES.md`](REFERENCES.md)。
> 若某项材料没有明确许可证，本项目不应发布它；请先补许可证或替换。
> 生成日期：2026-11-15。对应提交：`huella-x` / MoveScope（星动仪）。
> 官方要求：NASA Space Apps Legal 要求“所有资源都必须引用；第三方材料必须无限制可用或 OSI 开源；提交文档必须写明许可证”；未指定许可证的原创内容默认 Apache 2.0。原文：<https://www.spaceappschallenge.org/legal/>。

---

## 0. 本项目原创内容

| 内容 | 范围 | 许可证 |
|---|---|---|
| 本项目原创代码、文档、图表、截图、分析脚本 | `系统/app/`、`系统/数据库工具/`、`系统/视图工具/`（除 vendor/assets/第三方数据）、`项目主控/`、`README.md`、`系统/docs/01`、`系统/docs/02` 等 | **Apache License 2.0**（根目录 `LICENSE`；`NOTICE`） |

- 若未另行标注，原创内容按 Apache-2.0 分发。
- 截图（`系统/docs/img/*.png`、`系统/视图工具/docs/*.png`）是项目原创合成，包含 NASA 数据/地球纹理；第三方部分仍按下方归属。
- `系统/参考 资料/` 是第三方参考资料，**不**按 Apache-2.0 再许可；见 `系统/参考 资料/NOTICE.md`。

---

## 1. 第三方代码

| 组件 | 版本 | 许可证 | 来源 | 本项目文件 | 修改 |
|---|---|---|---|---|---|
| three.js | r160（`REVISION='160'`，minified ESM build） | **MIT** | <https://threejs.org/>；<https://github.com/mrdoob/three.js>；r160 `build/three.module.min.js` | `系统/视图工具/vendor/three.module.js`（重命名为 module，实际为 minified build） | 未修改，sha256 `3e690ac7d180b0aadf0891bea39eec643e29e2d3e75c99b18689518665f69ba6` |
| three.js 示例中的几何/着色器写法 | r160 | **MIT** | three.js examples（MIT） | `系统/视图工具/js/globe.js`、`js/skymap.js` 中的自绘实现参考了 three.js API/示例 | 本项目自行重写；不是复制文件 |

- three.js 完整 MIT 文本：`系统/视图工具/vendor/three.LICENSE`。
- three.js 文件头保留 `@license Copyright 2010-2023 Three.js Authors / SPDX-License-Identifier: MIT`。
- MIT 与本项目 Apache-2.0 兼容；three.js 的版权声明与许可文本随文件保留。

---

## 2. 图片 / 地球纹理

| 文件 | 尺寸 | 来源 | 许可证/条款 | 修改 |
|---|---|---|---|---|
| `系统/视图工具/assets/earth_atmos_2048.jpg` | 2048×1024 | three.js r160 `examples/textures/planets/`；原始地球影像：NASA Visible Earth / Blue Marble | three.js 仓库 **MIT**；NASA 影像按 NASA 媒体使用指南（一般不受版权保护；不得暗示 NASA 背书） | 与 r160 原文件 sha256 相同 |
| `earth_specular_2048.jpg` | 2048×1024 | 同上 | 同上 | 与 r160 原文件 sha256 相同 |
| `earth_clouds_1024.png` | 1024×512 | 同上 | 同上 | 像素与 r160 完全一致；仅 PNG 重新编码 |
| `earth_lights_2048.png` | 2048×1024 | 同上 | 同上 | 像素与 r160 完全一致；仅 PNG 重新编码 |

- three.js 纹理目录：<https://github.com/mrdoob/three.js/tree/r160/examples/textures/planets>
- NASA Visible Earth / Blue Marble：<https://visibleearth.nasa.gov/collection/1484/blue-marble>
- NASA 媒体使用指南：<https://www.nasa.gov/nasa-brand-center/images-and-media/>
- 说明：本项目的地球模型**代码**使用 three.js；地球**纹理**来自 three.js 示例（MIT），并标注 NASA 为原始影像来源。NASA 不背书本项目。
- `系统/视图工具/docs/preview.png`、`docs/evidence-movescope.png` 是项目截图，含上述纹理；按本项目 Apache-2.0 分发，第三方纹理仍按本表归属。

---

## 3. 数据

| 数据 | 来源 | 条款/引用 | 本项目文件 |
|---|---|---|---|
| SPHEREx level2 光谱像（QR3 / DR1）与切片 | NASA/IPAC IRSA；S3 公开桶 `nasa-irsa-spherex` | NASA/IPAC 公开数据；引用 IRSA SPHEREx 页面；不暗示 NASA 背书 | `系统/参考 资料/muestras/*.fits`、`系统/app/data/header_cache/*.hdr`、`epochcache/*.npz`、`系统/数据库工具/var/catalog.sqlite`、`var/blobs/*` |
| 目标坐标 / 真值（Barnard、Eps Ind B、WISE 0855、RW Aur、R Dra、M101、场 A） | SIMBAD/CDS（2026-11-14 查询） | 引用 Wenger et al. (2000), A&AS 143, 9, DOI <https://doi.org/10.1051/aas:2000332>；坐标事实数据 | `系统/app/data/targets.json`（及导出副本） |
| 赛道原文、官方目标清单、资源清单 | NASA Space Apps Challenge 2026 “Planet X and SPHEREx” | 官方页面版权归 NASA/Space Apps；仅作参考/合规引用；**不**按 Apache-2.0 再许可 | `系统/参考 资料/01`、`02`、`04`；见 `系统/参考 资料/NOTICE.md` |
| 观测样本头信息 | 由 NASA FITS 头派生 | NASA 数据条款；项目仅做本地索引/缓存 | `系统/参考 资料/muestras/cabeceras.txt`、`header_manifest.json` |
| 派生索引/目录：`week_index.json`、`epoch_catalog.json`、`targets.json`、`header_manifest.json`、`视图工具/data/{fields,targets}.json`、DB `catalog.sqlite`/`blobs` | 由 NASA SPHEREx 头/切片 + SIMBAD 坐标派生 | 原始数据按 NASA/CDS 条款；项目加工的索引文件按 Apache-2.0，但不得把原始数据说成项目自有 | `系统/app/data/`、`系统/视图工具/data/`、`系统/数据库工具/var/` |

- SPHEREx/IRSA：<https://irsa.ipac.caltech.edu/Missions/spherex.html>
- S3 公开桶：<https://nasa-irsa-spherex.s3.amazonaws.com/>
- 数据获取实测记录：`系统/参考 资料/03_SPHEREx数据访问笔记.md`
- 本项目只做本地索引/缓存/派生分析，不重新分发整幅原始观测；如需再分发，请遵循 IRSA/NASA 条款。
- 数据中的天体名称/坐标来自 SIMBAD；SIMBAD 的使用条款见 CDS。

---

## 4. 运行时依赖（未随仓库分发）

| 依赖 | 版本 | 许可证 | 来源 | 说明 |
|---|---|---|---|---|
| Python | 3.12.x | PSF License 2.0 | <https://www.python.org/> | 运行环境；不随仓库分发 |
| NumPy | 2.x | BSD-3-Clause | <https://numpy.org/> | 运行环境；不随仓库分发 |
| Pillow | 11.x | HPND | <https://python-pillow.org/> | 运行环境；不随仓库分发 |

> 本项目不 vendor 上述依赖；如果打包分发（例如把 Python 运行时一起打包），必须同时附上对应许可证文本。

---

## 5. 仅作参考/灵感、未复用代码或数据

| 项目 | 用途 | 许可证/说明 |
|---|---|---|
| WiseView（Backyard Worlds: Planet 9） | 闪烁/时间轴/可调视场的交互范式参考 | <http://byw.tools/wiseview>；未复制代码/数据 |
| bilawalsidhu/gods-eye-view | 3D 地球 + HUD 的视觉参考 | <https://github.com/bilawalsidhu/gods-eye-view>；未复制代码/数据；本项目用 three.js 自绘 |
| CDS Aladin | 天文可视化工具参考 | <https://aladin.cds.unistra.fr/>；未集成 |
| Astropy | 天文数据处理生态参考 | <https://www.astropy.org/>；未使用（本项目自写最小 FITS/WCS/测光） |
| Meeus, *Astronomical Algorithms* (2nd ed.) | 太阳位置/恒星时等公式参考（`js/astro.js`） | 书籍参考；未复制代码；公式为数学事实 |
| NASA Space Apps 官方页面 / Legal / FAQ | 赛道要求与合规条款 | <https://www.spaceappschallenge.org/legal/>、<https://www.spaceappschallenge.org/faq/>；官方文本不按 Apache-2.0 再许可 |

---

## 5.1 星表服务（阶段 B/D1；仓库仅含 234 行测试 fixture）

> 状态：B 星表服务已实现（`系统/数据库工具/db/star_*.py`、`catalog_server.py`），
> **仓库不包含 Gaia/2MASS/WISE/CatWISE/SIMBAD 全量数据**，也不包含 7 MB 的
> 17,679 行按场缓存（`var/star_catalog.sqlite` 已 gitignore）。唯一随仓库提交的
> Gaia 数据是 `系统/数据库工具/tests/fixtures/gaia_dr3_sample.csv`：**234 行真实
> Gaia DR3 TAP 结果子集，7 场抽样，抓取时间 2026-11-15T00:47:26Z，样本不完整，
> 仅用于离线测试/回归**，不得当作演示星表、全天星表或全量 Gaia。2MASS/WISE/
> CatWISE/SIMBAD 尚未接入数据；下表为接入前必须复核的条款。

| 数据 | 许可证/条款 | 来源 | 接入注意 |
|---|---|---|---|
| Gaia DR3 | **CC BY-NC 3.0 IGO**（非 OSI；非商业限制） | TAP <https://gea.esac.esa.int/tap-server/tap/sync>；Archive <https://gea.esac.esa.int/archive/>；许可 <https://www.cosmos.esa.int/web/gaia-users/license>；<https://doi.org/10.1051/0004-6361/202243940> | 需署名 ESA/Gaia DPAC；引用 Gaia Collaboration et al. 2023, A&A 674, A1；商业使用需单独确认；仓库只含 234 行测试 fixture，不重新分发全量/按场缓存 |
| 2MASS | NASA/IPAC 公开数据 | <https://www.ipac.caltech.edu/2mass/> | 署名 2MASS/IPAC |
| WISE / AllWISE | NASA/IPAC 公开数据 | <https://wise2.ipac.caltech.edu/> | 署名 WISE/IPAC |
| CatWISE2020 | NASA/IPAC 公开数据 | <https://doi.org/10.3847/1538-4365/abd805> | 署名 CatWISE |
| SIMBAD | CDS **ODbL**（署名/共享-alike） | <https://simbad.u-strasbg.fr/Files/public/CDS_SIMBAD_Terms_of_use_v1.0.pdf> | 若派生数据库对外分发，需按 ODbL 处理；名称/坐标引用 |
| NASA Exoplanet Archive | NASA 公开数据 | <https://exoplanetarchive.ipac.caltech.edu/> | 署名 NASA/Exoplanet Archive；仅宿主排除 |
| JPL Horizons / MPC | NASA/JPL、IAU MPC 公开数据 | <https://ssd.jpl.nasa.gov/horizons/>；<https://www.minorplanetcenter.net/> | 署名；查询限流 |

**合规风险提示**：Gaia 的 CC BY-NC 与 SIMBAD 的 ODbL 都不是 OSI 软件许可证；
它们作为“数据”使用，需按各自条款处理，并确认 NASA Space Apps Legal 对
“space agency partner data”的适用性。接入前不要把“未匹配”说成“新星”。

---

## 6. 字体与图标

- 本项目 CSS 使用系统字体栈；`--sans` 首选 `"Inter"`，但**不下载/不分发 Inter 字体文件**，缺失时回退到
  PingFang SC / Microsoft YaHei / system-ui 等系统字体。若本机安装了 Inter，其许可证为 SIL OFL 1.1
  （<https://openfontlicense.org/>），本项目未再分发。
- UI 图标为 CSS/Unicode/自绘 SVG；未使用第三方图标库。
- three.js 不包含本项目额外图标。

---

## 7. 合规声明

1. 本项目原创内容按 **Apache-2.0** 开源；未指定其他许可证。
2. 所有第三方代码/数据/图片/参考资料已在本文件或 `系统/参考 资料/NOTICE.md` 中引用；NASA 可据此核验。
3. three.js（MIT）与 Apache-2.0 兼容；NASA 数据/影像按 NASA 条款使用；SIMBAD 坐标按 CDS 引用。Gaia 星表服务已实现，但仓库只含 `系统/数据库工具/tests/fixtures/gaia_dr3_sample.csv` 的 **234 行真实测试 fixture**（CC BY-NC 3.0 IGO，不完整，仅测试）；全量/按场缓存与 SIMBAD 数据未随仓库分发，接入前需按 §5.1 复核许可。
4. 本项目不包含 export-controlled 信息，不包含第三方机密信息，不暗示 NASA 背书。
5. 若发现任何未列出的第三方材料，请在提交前补充到本文件并确认其许可证；NASA 可因未引用而取消提名/奖项。

---

## 8. 参考

- NASA Space Apps Legal: <https://www.spaceappschallenge.org/legal/>
- NASA Space Apps FAQ: <https://www.spaceappschallenge.org/faq/>
- OSI 开源定义: <https://opensource.org/docs/osd>
- Apache-2.0: <https://www.apache.org/licenses/LICENSE-2.0>
- MIT: <https://opensource.org/license/mit>
- NASA 媒体使用指南: <https://www.nasa.gov/nasa-brand-center/images-and-media/>
- NASA Visible Earth / Blue Marble: <https://visibleearth.nasa.gov/collection/1484/blue-marble>
- IRSA SPHEREx: <https://irsa.ipac.caltech.edu/Missions/spherex.html>
- SIMBAD/CDS: <https://doi.org/10.1051/aas:2000332>
- three.js: <https://threejs.org/> / <https://github.com/mrdoob/three.js>
