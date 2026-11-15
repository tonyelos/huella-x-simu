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
| app | `cd 系统/app && $PY -m unittest discover -s tests` | **125 tests OK** |
| DB | `cd 系统/数据库工具 && $PY -m unittest discover -s tests -t .` | **99 tests OK** |
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
- Ego 发现 1 个 P1 provenance 文案矛盾（Barnard fov=0.5 的“6 天/204 天”），已由 app `5d52472` 修复；修复后 T1 仍 45/0/1，app **125 tests**。

截图：[evidence-ego/](evidence-ego/)。

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
3. 星点选择与已知/未知恒星识别（Gaia/2MASS/WISE + DB 星表同步）：见 [../架构/05_星点选择与识别路线图.md](../架构/05_星点选择与识别路线图.md)。
4. T1 截图自动化（Playwright/ego-browser 可选）。
5. T6 readiness 阈值统一（180d vs 0.6yr）或删除旧字段。
6. 公网部署：鉴权、限流、CORS 收紧、`include_truth=0`。
