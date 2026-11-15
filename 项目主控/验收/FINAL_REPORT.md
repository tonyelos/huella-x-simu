# 最终验收报告 · Planet X and SPHEREx

> 时间：2026-11-15 00:45 CET
> 仓库：<https://github.com/tonyelos/huella-x-simu>（private）
> 集成分支：`main`；所有工具分支已合并；工作树干净。
> 本文是“当前可交付状态”；`项目主控/01_验收基线与结果.md` 是 23:07 的审计快照，保留历史。

---

## 0. 结论

**首轮闭环完成。** R1/R2 已打通，R3 的 5 个官方目标全部可跑且“手动输入坐标 = 点预设”，R4 无 500、BIC/历元可见；T1 验收 P0 全绿。剩余是数据/加分项边界，不是阻断缺陷。

| 要求 | 状态 | 证据 |
|---|---|---|
| R1 任意 RA/Dec → 该天区多期图像 | ✅ | 自定义 Barnard `REAL/4/L2/S`；DB `/api/v1/epochs` 两入口一致；无覆盖显式 SIM |
| R2 多期“小视频” | ✅ | 闪烁/叠加/相减/轨迹、时间轴、离线 5/5 |
| R3 5 个指定天体 | 🟡 部分（真实数据 3/5） | 5/5 可跑；Barnard S、Eps L、WISE U、RW/R Dra V；RW/R Dra 为 HYBRID |
| R4 面向公众 | ✅（有边界） | 无 500、BIC/历元面板可见、中文公众话术；T1 截图仍 manual WARN |
| 免费/加分：离线/URL/相加相减/FOV | ✅ | T3 离线 5/5；视图深链；FOV 0.02–3.0 |
| 加分：102 波段/光谱/Gaia | ❌/未做 | 只有波段标签；无实时光谱、无 Gaia/WISE 叠加 |

![最终界面：Eps Ind B REAL/L3/L，研究者模式显示 BIC 与历元](<../../系统/docs/img/ui-final.png>)

**重要边界**：R1 的真实数据覆盖限于本地 5,893 帧 / 5,562 曝光；`week_index` 有 151 周扫描索引，但其中只有 **23 周**有本地帧（不是全天任意天区都有真实数据）；覆盖外会明确标 SIM，不冒充真实。

---

## 1. 分支与工具

每个工具一个分支、一个子 agent；分支从 `main` 切出，完成后由集成者合并。

| 工具 | 分支 | 负责人 | 关键提交 | 合并 |
|---|---|---|---|---|
| MoveScope 产品 | `tool/app` | app-owner | `f0034bc`（RW 越界/schema/FOV/测试）、`cc0f9c7`（自定义=预设） | ✅ |
| 数据库目录/API | `tool/db` | db-owner | `eb2febf`（v1/NaN/coverage）、`028236f`（v1 targets/truth）、`0d492a0`（文档） | ✅ |
| 天区视图 | `tool/view` | view-owner | `4a45807`（深链/单一源/fields/口径） | ✅ |
| T1 验收/契约 | `tool/t1-acceptance` | t1-owner | `18f24f3`（BLOCKED）、`5f0a68c`、`b8c684d`（R3 等价） | ✅ |
| T2 数据桥接 | `tool/t2-bridge` | t2-owner | `b6a7316`（只走 v1、DTO/降级） | ✅ |
| T3 离线冻结 | `tool/t3-freeze` | t3-owner | `be18b13`（迁移后复核） | ✅ |
| T4 头文件采集 | `tool/t4-harvest` | t4-owner | `4f1a447`（P0-1/2/3 + 测试/docs） | ✅ |
| T5 统一启动 | `tool/t5-launcher` | t5-owner | `b09ca70`（迁移后 3/3） | ✅ |
| T6 漂移检查 | `tool/t6-drift` | t6-owner | `f8f8814`（新建）、`a9132a3`（误报修复） | ✅ |

**共享约束**：子 agent 只在各自 worktree 工作；不切分支/不 reset main；只改自己拥有的文件；提交并推送 `tool/<name>`；集成者合并到 main 并跑 T1/T3/T5/T6。

---

## 2. 最终验收证据

### 2.1 T1 验收（main）

```text
PASS=45 FAIL=0 WARN=1 BLOCKED=0 P0_FAIL=0 P0_BLOCKED=0 P1_FAIL=0
耗时 49.2s；退出码 0
```

- 覆盖：契约、5 目标、R1 自定义、R3 自定义=预设、错误路径、离线 5 目标、DB 导出对账、DB v1 一致性。
- 唯一 WARN：`H.screenshot` manual（不引入 Playwright，视图工具不进提交硬门槛）。
- 报告：`项目主控/验收/report/final/report.md`（运行产物，不提交）。

### 2.2 单测/组件

| 组件 | 命令 | 结果 |
|---|---|---|
| app | `cd 系统/app && $PY -m unittest discover -s tests` | **271 tests OK** |
| DB | `cd 系统/数据库工具 && $PY -m unittest discover -s tests -t .` | **126 tests OK** |
| DB 完整性 | `$PY cli.py verify --deep` | quick ok / 0 FK / 0 missing / 0 corrupt |
| DB 导出对账 | `check_compat.py --root ..` | fields 5,893 / epochs 74 / targets 7 / weeks 151 全过 |
| view | `$PY tools/smoke.py` | **全部 PASS / 0 warning** |
| T3 | `verify --strict` + `run_offline --targets all` | 27/27 sha；**5/5 PASS**；7/7（含 2 坐标） |
| T5 | 默认 `status.sh` | DB/app/view **3/3**；sandbox 3/3；stop 端口全释放 |
| T6 | `项目主控/漂移/run.sh` | **0 ERROR**；1 WARN（DB 旧 readiness 阈值 180d vs app 0.6yr） |

### 2.3 R1 / R3 关键实测

| 输入 | mode | n | real | readiness | class |
|---|---|---|---|---|---|
| 自定义 Barnard `269.452077,4.693365` | real | 4 | 4 | L2 | S |
| 自定义 Eps Ind B `331.044147,-56.782844` | real | 8 | 8 | L3 | L |
| 自定义 WISE 0855 `133.795129,-7.245147` | real | 8 | 8 | L0 | U |
| 自定义 RW Aur `76.956526,30.401438` | hybrid | 8 | 0 | L2 | V |
| 自定义 R Dra `248.167611,66.754979` | hybrid | 8 | 0 | L2 | V |
| 未覆盖 `0,0` | synthetic | 8 | 0 | L2 | S（`NO_LOCAL_COVERAGE`） |

**5/5 自定义坐标与预设一致**（mode/readiness/class；μ ±5%）。RW/R Dra 的 `real_epochs=0` 是 HYBRID 纯 SIM 回退的正确结果，不是缺陷。

### 2.4 5 目标预设

| 目标 | mode | n | readiness | class | μ (mas/yr) |
|---|---|---|---|---|---|
| Barnard | real | 4 | L2 | S | 8033 ± 9899 |
| Eps Ind B | real | 8 | L3 | L | 4707 ± 547 |
| WISE 0855 | real | 8 | L0 | U | — |
| RW Aur | hybrid | 8 | L2 | V | 49 ± 2116 |
| R Dra | hybrid | 8 | L2 | V | 795 ± 2437 |

---

### 2.5 Ego 浏览器 UI 复核

2026-11-15 00:52–01:00 在 Ego Lite v2.2.1 中实测（详见 [Ego_UI_验证.md](Ego_UI_验证.md)）：

- 5 目标 UI 点击 → REAL/REAL/REAL/HYBRID/HYBRID，判定 S/L/U/V/V；BIC/历元列表在研究者模式可见。
- 自定义 Barnard = REAL/4/L2/S；自定义 RW = HYBRID/8/L2/V；未覆盖 = SIM + `NO_LOCAL_COVERAGE`。
- sample 模式 = SAMPLE/U；非法 FOV 有可读提示、无 500；四种显示模式可切换；播放 slider 0→3。
- 视图深链 `?ra=269.452077&dec=4.693365&fov=0.5&target=barnard` → 选中 Barnard；“在 MoveScope 打开”弹窗 → REAL/L2/S。
- Ego 发现 1 个 P1 provenance 文案矛盾（Barnard fov=0.5 的“6 天/204 天”），已由 app `5d52472` 修复；修复后 T1 仍 45/0/1，app **271 tests**。

截图：[evidence-ego/](evidence-ego/)。

2026-11-15 04:48 补充 GUI 链路测试：[GUI链路测试.md](GUI链路测试.md)（C 覆盖 → C 源 → 锁定 → 识别 → 视图深链；发现并修复 note 残留 P1）。

单图星点识别测试：[单图星点识别测试.md](单图星点识别测试.md)（Barnard 单期 47 个检测源 vs 2,583 Gaia；G<14 完整度较高，G>16 基本不可检测）。

官方案例亮度对比：[官方案例亮度对比.md](官方案例亮度对比.md)（R Dra W1=0.39 到 WISE 0855 W1=16.14，跨度约 15.8 mag；不是同一亮度范围）。

官方识别方法调研：[../架构/08_官方识别方法调研.md](../架构/08_官方识别方法调研.md)（Source Discovery/SExtractor、MOST、forced photometry、Backyard Worlds/WiseView）。

图像/多波段策略：[../架构/09_图像与多波段策略.md](../架构/09_图像与多波段策略.md)（cutout/饱和/深度诊断；不要找“更饱和”的图；分 detector stack + SED）。

层级识别/残差策略：[../架构/10_层级识别与残差策略.md](../架构/10_层级识别与残差策略.md)（L1 检测 → 已知源 PSF 扣除 → residual 再检测；不是删除已知源）。

级联/残差页已实现：`/cascade`（Barnard 4 历元 / 215 L1 / 215 masked / 241 residual；5σ ≤80/期；页面明确“残差候选 ≠ 新天体”）。见 [GUI链路测试.md](GUI链路测试.md) §5。

多历元层级发现：[../架构/11_多历元层级发现.md](../架构/11_多历元层级发现.md)（tracklet + D0–D5；候选发现，不是自动确认）。

三模块联动：[../架构/13_三模块联动计划.md](../架构/13_三模块联动计划.md)（View 时间窗口 → App `/cascade`/`/tracks` 过滤 → DB epochs/coverage）。见 [GUI链路测试.md](GUI链路测试.md) §7。

外部数据包审查：[外部数据包审查_sphx_b.md](外部数据包审查_sphx_b.md)（原始 DR1 recorte 可用；6 个派生候选仅 2 历元、全部未识别，不能当发现）。

模式化重构：[../架构/14_模式化重构计划.md](../架构/14_模式化重构计划.md)（View 只选区域/时间；MoveScope 同画布切换普通/级联/轨迹）。

模式化重构已完成并验证：View 只剩“在 MoveScope 打开”；MoveScope 主画布模式栏切换普通/级联/轨迹，URL 不跳页。见 [GUI链路测试.md](GUI链路测试.md) §8。

时间轴优化计划：[../架构/15_时间轴与播放优化.md](../架构/15_时间轴与播放优化.md)（UTC 输入、观测序列播放、5 刻度时间轴、统一时间源）。

时间轴/播放优化完成：UTC 输入、±1d/±1h、5 个 UTC 刻度；播放按用户要求回退为连续时间（0.15×/1×/4×，末尾回绕），手动改时间保留 700ms tween。见 [GUI链路测试.md](GUI链路测试.md) §9。

官方案例基准优化：[../架构/16_官方案例基准与机制优化.md](../架构/16_官方案例基准与机制优化.md)（5 个官方实例只读基准；Barnard 不应当裸判 S）。

官方案例基准完成：Barnard 从 S 改为 L（`catalog_prior`，保留实测 μ 与未独立测出 caveat）；5 案例 4 PASS + 1 DATA_LIMITED。见 [官方案例基准_report.md](官方案例基准_report.md)、[GUI链路测试.md](GUI链路测试.md) §10。

数据接收接口：[../架构/17_数据接收接口与自动缓存.md](../架构/17_数据接收接口与自动缓存.md)（Gaia TAP → SQL ingest → app 自动重试）。

数据接收接口已实现：B `POST /api/v1/catalog/ingest` 场级 TAP→SQL；app `CATALOG_AUTO_INGEST=1` 可自动 ingest+重试一次；DB 126 tests / app 271 tests。TAP 当前超时只验证 502 不写，fake TAP 路径通过。

全量缓存功能验证：B 17,679 源/7 场/schema v3；C 47 源 → 31 single/6 ambiguous/10 no_match；tracks D1=11；官方基准 4 PASS + 1 DATA_LIMITED（WISE 20.3″ 候选被 6.15″ 门限拒绝）。见 [官方案例基准_fullcache.md](官方案例基准_fullcache.md)、[星表识别覆盖率.md](星表识别覆盖率.md)。

批量星表识别计划：[../架构/18_批量星表识别.md](../架构/18_批量星表识别.md)（C 源表 47 源批量匹配 → known/ambiguous/no_match UI）。

批量星表识别已实现：C 源表 111 源 → known 61 / ambiguous 7 / no_match 43；UI badge + 计数；47 单期基线为 31/6/10。见 [GUI链路测试.md](GUI链路测试.md) §12。

A 源表批量识别已实现：215 源 → known 130 / ambiguous 33 / no_match 48 / insufficient 4；A/C badge 在左栏内可见；见 [GUI链路测试.md](GUI链路测试.md) §13。

批量结果审计导出已实现：A 215 行/C 111 行 CSV（含 RUWE、2MASS/WISE 列；ambiguous 空 catalog_id；no_match 未收录候选）；见 [GUI链路测试.md](GUI链路测试.md) §14。

星表可用性检查：[星表可用性检查.md](星表可用性检查.md)（7 场/17,679 源可用；任意新天区 503；不是全量 Gaia）。

批量结果审计导出计划：[../架构/20_批量结果审计导出.md](../架构/20_批量结果审计导出.md)（CSV/JSON，含 RUWE 与 J/H/K/W1/W2）。

A 源表批量识别计划：[../架构/19_A源表批量识别.md](../架构/19_A源表批量识别.md)（Barnard 215 源分 200+15 批）。

星表识别覆盖审查：[星表识别覆盖率.md](星表识别覆盖率.md)（当前 B 250 行 fixture → 0/47；完整 17,679 缓存 → 37/47 有候选；检测仅覆盖约 1.8% 真实星）。

跨历元轨迹实施计划：[../架构/12_跨历元轨迹发现计划.md](../架构/12_跨历元轨迹发现计划.md)（T1 tracklet → T2 UI → T3 验证）。

跨历元轨迹页已实现：`/tracks`（Barnard 4 期：D0 45 / D1 0 / D3 10 / D4 195；D1=0 因 B fixture partial；轨迹候选 ≠ 新天体）。见 [GUI链路测试.md](GUI链路测试.md) §6。

三模块联动计划：[../架构/13_三模块联动计划.md](../架构/13_三模块联动计划.md)（View 选区/时间 → App cascade/tracks → DB epochs/coverage）。

### 2.6 D-MVP 已知/未知识别

- A 锁定检测源后，app 调用 `/api/identify` → 独立星表服务 8799 → Gaia DR3 `match`。
- 状态：`known_star / ambiguous / uncatalogued_candidate / insufficient_data / catalog_unavailable`；全部 `discovery_claim=false`。
- 已知源显示 `source_id`/release/角距/置信度；未匹配只标“未收录候选”，不是“新恒星”。
- 测试：app **271 tests OK**（含 fake catalog HTTP、503/partial、source_id 字符串、UI 文案）；DB **126 tests OK**。
- B v2：RUWE/astrometric excess noise/phot_variable_flag + 2MASS/WISE 测光；D 对 `RUWE>2` 降级为 ambiguous，`CATALOG_PERSIST=1` 时可选写 `catalog_match`。
- 边界：B 的 7 MB Gaia 按场缓存不提交；真实演示需先 `import_gaia.py --rebuild`；2MASS/WISE 覆盖约 47.6%/45.5%，缺失保持 null；持久化默认关闭，`CATALOG_PERSIST=1` 才写。

### 2.7 C-P0 / C-P1A 本地覆盖与源列表

- 独立只读服务 `系统/anyfield/`，默认 8798；只读 `catalog.sqlite`，不联网、不写库。
- **74 条有 header 的历元**用真实 WCS 四角足迹（TAN；未应用 SIP）；其余 5,819 帧仍 `center_approx`。
- `/api/v1/field`：7 个预设场 200 + 本地 EpochRef；未知天区 **503 `INDEX_INCOMPLETE`**；422 只在显式 `coverage_region.complete=1` 的 fixture 中成立，生产库不假装“确认空白”。
- `POST /api/v1/ingest`：P0 支持本地 JSON manifest；P1A 支持本地 NPZ/FITS（allowed roots、202 异步、幂等、软回滚），用 app `detect_sources` 产出源列表。
- `/api/v1/sources`：C-native 检测源；实测 Barnard 4 期 ingest 后 215 sources。
- P1B：`/api/v1/discover`（week_index + S3 64KB 头）与 `/api/v1/fetch`（单张 S3/IRSA cutout，allowlist/大小上限）；源 `source_origin=on_demand_s3`。
- 测试 **45 tests OK**；app 271、DB 126。
- 边界：完整天空索引、多历元重投影/共同网格、series 重算仍未实现；C 不是“任意天区完成”。

### 2.8 D-C P0 覆盖握手

- app 新增 `core/anyfield.py` + `GET /api/field` 代理，调用 C 8798；超时 1.5s。
- 200：显示 C 覆盖（Barnard 4 期、`footprint_method=wcs_header`）；422：确认无覆盖；503：显示“C 索引不完整，使用现有本地数据”；连接失败：显示 C 不可用。
- **不阻塞分析**、不改变 A 源列表/D 识别；C 失败只影响覆盖卡。
- 边界：C-native 源列表/像素/增量仍未做（C-P1）。

### 2.9 D-C P1 源列表集成

- C-P1A 本地 ingest（NPZ/FITS）后，`/api/v1/sources` 返回 C-native 检测源。
- app `/api/sources` 代理；UI C 覆盖卡可加载源列表，点击源 → 锁定 → `/api/identify`。
- 实测：C 8798 已 ingest Barnard 4 期 215 sources；app `/api/sources` 返回 5/188；C 源识别受 B fixture `partial` 影响，诚实返回 `insufficient_data`。
- 边界：C-P1A 只本地 NPZ/FITS；无 IRSA/S3/重投影/公共网格；C 源是检测源，不是 known/unknown 分类。

## 3. 工具定义（最终）

| 工具 | 职责 | 不做什么 |
|---|---|---|
| MoveScope `系统/app` | 取数/测光/相对定标/关联/BIC/公众话术/UI | 不做全天巡天、不做通用星表平台 |
| DB `系统/数据库工具` | SQLite 元数据 + Blob + v1 历元/覆盖/目标 API + 导出 | 不做测光/分类；不渲染 |
| View `系统/视图工具` | 三维地球 + 天区选择 + 深链到 MoveScope | 不渲染 FITS、不做测量 |
| T1 `项目主控/验收` | 契约/5 目标/R1/R3/错误/离线/DB 一致性验收 | 不改源码；不替代科学判定 |
| T2 `系统/app/core/epoch_source.py` | 任意坐标 → DB v1 EpochRef → 管线 | 不写 DB；不做科学 |
| T3 `项目主控/冻结` | 5 目标 + 坐标离线快照/manifest/校验 | 不采整幅 FITS；不改管线 |
| T4 `系统/数据库工具/tools/harvest_headers.py` | S3 Range 64KB 头 → DB | 不下整幅 65MB；不写生产库（验证用临时库） |
| T5 `项目主控/启动` | 固定端口启动三服务/健康/停止 | 不自动换端口；不杀别人进程 |
| T6 `项目主控/漂移` | 只读跨工具 targets/epochs/fields/常量/文档/schema 检查 | 不修源码；只报告 |

---

## 4. 已知限制（不阻断提交，但答辩要讲）

1. **R1 覆盖有限**：`week_index` 151 周、其中 23 周有本地帧（5,893 帧）；实时 IRSA 发现新历元仍受 TAP/SIA/DataLink 502 影响；覆盖外显式 SIM。
2. **R3 真实数据只有 3/5**：Barnard 饱和判 S、WISE 仅 1 期判 U、RW/R Dra 真实历元不足走 HYBRID。工具诚实标注，不硬报运动。
3. **102 波段/实时光谱未做**：小切片只采样窄波段；需要大切片/整幅 + LVF 波长插值。
4. **Gaia/WISE 叠加未做**；污染标记只到质量标记/掩膜层，未做逐像素 flags 图层。
5. **大 FOV 慢**：`fov=3.0` 真实数据全管线仍有 Python 级检测循环，演示用 0.2–0.5°。
6. **T1 截图 manual**；T6 有 1 条 readiness 阈值 WARN（旧字段已 deprecated）。
7. **无 git 之前的回滚**：迁移前备份在 `项目主控/_归档/2026-11-14-2351/pre-migration.tar.gz`；现在已有 GitHub 历史。

---

### 4.1 许可与引用（提交合规）

- **原创代码/文档/截图**：Apache License 2.0（根目录 `LICENSE`、`NOTICE`）。
- **第三方完整清单**：`THIRD_PARTY_NOTICES.md`；提交表单简版：`REFERENCES.md`。
- **three.js r160**：MIT；完整文本 `系统/视图工具/vendor/three.LICENSE`。
- **地球纹理**：three.js r160 examples（MIT）；原始影像 NASA Visible Earth / Blue Marble，
  按 NASA 媒体条款；截图/文件级归属见 `THIRD_PARTY_NOTICES.md`。
- **SPHEREx 数据**：NASA/IPAC IRSA（QR3/DR1）；SIMBAD/CDS 坐标；引用 DOI
  `10.1051/aas:2000332`。
- **参考资料**：`系统/参考 资料/` 为第三方材料，不按 Apache-2.0 再许可；
  见 `系统/参考 资料/NOTICE.md`。
- **官方条款**：<https://www.spaceappschallenge.org/legal/>、
  <https://www.spaceappschallenge.org/faq/>；所有第三方材料已引用，
  第三方许可证均为 OSI 认可或 NASA 公开数据；NASA 不背书本项目。

## 5. 复现命令

```bash
cd /Users/yiao/Documents/huellaX
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

# 1) 启动
项目主控/启动/start_all.sh          # DB 8797 / app 8790 / view 8830
项目主控/启动/status.sh

# 2) 最终验收
项目主控/验收/run.sh --base-url http://127.0.0.1:8790 \
  --db-url http://127.0.0.1:8797 --view-url http://127.0.0.1:8830 --offline

# 3) 离线
$PY 项目主控/冻结/run_offline.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --targets all

# 4) 漂移
项目主控/漂移/run.sh

# 5) 单测
cd 系统/app && $PY -m unittest discover -s tests
cd ../数据库工具 && $PY -m unittest discover -s tests -t .
cd ../视图工具 && $PY tools/smoke.py
```

---

## 6. 下一轮（可选）

1. 真实数据补齐：Barnard 未饱和波段、WISE 坐标歧义、RW/R Dra 更多真实历元。
2. 102 波段/光谱：大切片 + LVF 插值；按 6 阵列合图。
3. 星点选择与已知/未知恒星识别：A 已合并、D-MVP 已合并（app 271 tests）；C-P0/P1A/P1B（8798，45 tests）已合并；多历元重投影/series 仍未实现，见 [../架构/05_星点选择与识别路线图.md](../架构/05_星点选择与识别路线图.md)。
4. T1 截图自动化（Playwright/ego-browser 可选）。
5. T6 readiness 阈值统一（180d vs 0.6yr）或删除旧字段。
6. 公网部署：鉴权、限流、CORS 收紧、`include_truth=0`。
