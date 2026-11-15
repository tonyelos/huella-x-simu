# Ego 浏览器 UI 验收记录

> 时间：2026-11-15 00:52–01:00 CET
> 浏览器：Ego Lite v2.2.1（`ego-browser skill` 指南 v2.2.1），TaskSpace 57
> 环境：`项目主控/启动/start_all.sh` 启动 DB 8797 / app 8790 / view 8830
> 目的：在真实浏览器里复核 T1 之外的 UI 行为，确认“公众可用、5 目标、任意坐标、深链”不是只在 curl 层成立。

---

## 1. 测试结果

### 1.1 五个官方目标（UI 点击预设 → 识别）

| 目标 | 徽章 | 就绪度 | 判定 | 期数 | BIC 可见 | quality |
|---|---|---|---|---|---|---|
| Barnard's Star | REAL | L2 | S | 4 | ✅ M0 60% / M2 35% / M4 6% | SATURATED_SOURCE, LARGE_CENTROID_ERROR |
| Epsilon Indi B | REAL | L3 | L | 8 | ✅ M2 45% / M3 33% / M4 23% / M0 0% | LARGE_CENTROID_ERROR |
| WISE 0855 | REAL | L0 | U | 7–8（IRSA 偶发 1 期失败） | —（无模型，隐藏合理） | FEW_EPOCHS, SHORT_BASELINE, LARGE_CENTROID_ERROR |
| RW Aurigae | HYBRID | L2 | V | 8 | ✅ M0+SIN 96% / M2+SIN 4% | LARGE_CENTROID_ERROR |
| R Draconis | HYBRID | L2 | V | 8 | ✅ M0+SIN 94% / M2+SIN 5% | LARGE_CENTROID_ERROR |

- 5/5 HTTP 200，无 JS console error（`window.__errs=[]`）。
- 研究者模式打开后，Barnard/Eps/RW/R Dra 的 BIC 面板 `display:flex`、模型行非空；WISE 因无模型隐藏，符合预期。
- 历元列表（`#epochList`）4/8/7/8/8 行；点击可切换。
- 公众话术、置信度、可证伪条件在右栏可见。

### 1.2 任意坐标（R1）

| 输入 | 模式 | 期数 | 就绪度 | 判定 | 说明 |
|---|---|---|---|---|---|
| 自定义 Barnard `269.452077,4.693365,fov .25` | REAL | 4 | L2 | S | 0.05° 命中 barnard，与预设一致 |
| 自定义 RW Aur `76.956526,30.401438,fov .5` | HYBRID | 8 | L2 | V | 0.05° 命中 rw_aur，与预设一致 |
| 未覆盖 `0,0,fov .25` | SIM | 8 | L2 | S | 明确 `NO_LOCAL_COVERAGE`，不冒充真实 |
| 非法 FOV `-0.2` | — | — | — | — | UI 提示“视场 FOV 需在 0.02–3.0 度之间”，无 500 |

### 1.3 模式与播放（R2）

- 闪烁/叠加/相减/轨迹四个按钮均可切换，active 状态正确，canvas 存在。
- Barnard 4 期，点击播放 2.5 s 后 slider 从 0 → 3，`#epochLabel` 从第 1 期跳到第 4 期，canvas dataURL 变化 → 动画确实在播。
- 暂停按钮切回 ▶；时间轴可拖动。

### 1.4 视图工具（体验层）

深链：

```text
http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&target=barnard
```

浏览器 DOM：

```text
title    SPHEREx 天区视图 · MoveScope
RA/Dec   269.45 / 4.69  → 17h57m48.5s / +04°41′36″
FOV      0.50° × 0.50°
本地命中 10 帧 · 5 曝光 · 3 夜（2.47°）
策展历元 4 · 数据库 epoch_catalog
```

点击“在 MoveScope 打开” → 弹出新页：

```text
http://127.0.0.1:8790/?target=barnard&ra=269.452077&dec=4.693365&fov=0.5
modeBadge  REAL 真实
readyBadge L2 就绪度
verdict    S 静止（无明显位移），置信度 72%
```

深链端到端成立。

---

## 2. 发现与修复

### P1 · 已修复：provenance 文案“6 天 vs 204 天”矛盾

Ego 首轮发现 Barnard `fov=0.5`：

```text
data.n_epochs=4, result.detected_epochs=3, result.baseline_days=6.451
data.note 仍写 “实取 4/4 期，基线 204 天”
UI sourceNote: “REAL · 4 期 · … · 6 天基线 · 真实 4/4 期 · … 基线 204 天”
```

WISE 同类：`detected_epochs=1`、`baseline_days=0`，note 写“基线 183 天”，UI 显示“0 天基线”。

app branch `5d52472` 修复：

- API 新增 `fetch_attempted/fetch_succeeded/detected_epochs/detection_baseline_days/catalog_baseline_days`。
- `note` 改为“取数 4/4 期；其中 3 期用于拟合，探测基线 6 天（目录基线 204 天）”。
- UI `sourceNote` 改为“取数 4/4 期 · 拟合 3/4 期 · 探测基线 6 天”，目录基线只在 note 带限定词出现。
- 修复后 Ego 复测：

```text
REAL · 4 期 · D1/D2/D3 · 取数 4/4 期 · 拟合 3/4 期 · 探测基线 6 天 · 6.2s
IRSA 静态切片取数 4/4 期；其中 3 期用于拟合，探测基线 6 天（目录基线 204 天）；参考星相对定标。
```

### 非阻断观察

1. 首轮 Barnard `fov=0.25` 遇到 1 期 IRSA 取数失败，显示“真实 3/3 期，另有 1 期失败”；第二轮 4/4。工具诚实标注，但网络抖动会影响演示，建议演示前预热缓存。
2. WISE 无模型时 BIC 面板隐藏是正确行为；不是 bug。
3. T1 的 H.screenshot 仍 manual WARN；本次 Ego 已补真实截图证据。
4. Ego 截图是视口截图（1458×703），不是全页长图；足以为 UI 验收留证。

---

## 3. 截图

| 文件 | 内容 |
|---|---|
| [app-barnard-provenance-fixed.png](evidence-ego/app-barnard-provenance-fixed.png) | 修复后 Barnard REAL/L2/S；取数 4/4、拟合 3/4、探测基线 6 天 |
| [app-barnard-bic-ego.png](evidence-ego/app-barnard-bic-ego.png) | 研究者模式 BIC/历元列表 |
| [view-barnard-ego.png](evidence-ego/view-barnard-ego.png) | 视图工具深链 Barnard、10 帧/5 曝光/3 夜 |
| [app-barnard-ego.png](evidence-ego/app-barnard-ego.png) | 修复前证据（6 天/204 天矛盾） |

---

## 4. 结论

- **R1/R2/R3/R4 在真实浏览器里成立**：5 目标、任意坐标、播放、模式、BIC、深链均通过。
- **Ego 发现并修复了 1 个 P1 provenance 文案问题**；修复后 T1 仍 **45 PASS / 0 FAIL / 1 WARN**，app **111 tests OK**。
- 剩余都是已知数据/加分项边界（真实数据 3/5、无光谱/Gaia、`fov=3.0` 慢），不是 UI 阻断。
