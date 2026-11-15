# GUI 链路测试 · Ego 浏览器

> 时间：2026-11-15 04:40–04:48 CET
> 浏览器：Ego Lite（Chromium；TaskSpace 60）
> 环境：app 8790 / DB 8797 / view 8830 / C 8798（P1B）/ B 8799（v2 250 行 fixture）
> 目的：验证“C 覆盖卡 → C 源列表 → 锁定源 → Gaia 识别 → 视图深链”在真实浏览器里是否打通。

---

## 1. 测试结果

### 1.1 主应用：Barnard 分析

- 打开 <http://127.0.0.1:8790/>，选择 Barnard，点击“识别运动规律”。
- 结果：`REAL 真实 / L2 / S / 置信度 72%`；`4 期，取数 4/4，拟合 4/4，探测基线 204 天`。
- 画布出现检测源圆点/十字；目标有“目标”标记；无 JS console error。
- 右侧“星表识别 · 目标源”：
  - `Gaia DR3 已知`；
  - `source_id=4472832130942575872`；
  - `RUWE 1.08`、`CATALOG_PARTIAL`、`HIGH_PM`、`NO_PM_CHECK`；
  - 明确写“未匹配不等于新天体；不做发现声明”。

### 1.2 C 覆盖卡

- C 8798 在线，app 自动并行调用 `/api/field`。
- 卡片显示：`C 覆盖可用`、`4 期 · footprint wcs_header · 索引 partial`。
- 没有把 `INDEX_INCOMPLETE` 说成“无覆盖”；没有 SIM。

### 1.3 C 源列表 → 锁定 → 识别

- 点击“加载 C 源列表”：返回 **111 个**（显示前 50），来源 `ingested`。
- 点击第一个 C 源：
  - 锁定卡片：`已锁定源`、RA/Dec、S/N、flux、`C ingested`、`质量良好`；
  - `#sourceNote`：**修复后**为
    `已锁定 C 源 RA 269.392799° · Dec 4.772089° · 数据不足（insufficient_data）`；
  - 识别结果：`数据不足 / gaia-dr3 / 星表覆盖不完整，0 候选不能当“未收录”`；
  - 没有出现“正在查询”残留，没有“新天体/行星”。

> 当前 B 8799 是 **250 行 fixture + partial 场缓存**，所以 C 源识别常见 `insufficient_data` 是诚实降级，不是链路断了。

### 1.4 视图工具深链

- 打开 `http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&target=barnard`。
- 选中 Barnard；本地命中 **10 帧 / 5 曝光 / 3 夜**；策展历元 4。
- 点击“在 MoveScope 打开” → 新窗口：
  `http://127.0.0.1:8790/?target=barnard&ra=269.452077&dec=4.693365&fov=0.5`
- 新窗口：`REAL/L2/S`，C 覆盖卡 `C 覆盖可用`；无 console error。

---

## 2. 发现的问题

### P1 · 已修复：C 源锁定后 `#sourceNote` 永久停留“正在查询”

- 现象：识别结果已返回并显示在 `#lockInfo`，但左侧 `#sourceNote` 仍是
  “已锁定 C 源 …；正在查询 Gaia DR3…”。
- 原因：`identifyLockedSource()` 的 finally 只更新 lock 卡，没有更新 sourceNote。
- 修复：`identify-owner` commit `d2ae9bb`，新增 `lock_note.js` 纯函数；成功/失败/超时后更新 note；只对 C 源更新，不破坏 A 源“重新分析”提示。
- 复测：`loading=false`，note 显示 `数据不足（insufficient_data）`；app **207 tests OK**。

### 开放问题（不是本次能修完的）

1. **C-P1B 的 discover/fetch 没有 GUI 入口**  
   现在 C 覆盖卡只有“加载 C 源列表”，只能读**已 ingest** 的源；任意天区没有“发现/取数”按钮。  
   `/api/v1/discover` 和 `/api/v1/fetch` API 已实现（45 C tests），但 GUI 链路还没有把它们接进按钮/自动流程。
2. **C 源列表延迟约 4 s**  
   点击后按钮显示“加载 C 源列表…”，最终 111 个源；没有进度/取消提示，演示时需等待。
3. **C 源锁定 URL 不保留 `source_id`**  
   现在 URL 只有 `lock=1` + RA/Dec；刷新不能精确恢复 C 源卡片。
4. **B 仍是 250 行 fixture**  
   Barnard 目标源可 known；普通 C 源常 `insufficient_data`。真实 17,679 行缓存需本地 `import_gaia.py --rebuild`。
5. **C 源识别没有质心误差**  
   UI 用 σ=0.5″ 保守值；结果带 single-epoch/no-PM caveat 是预期的。
6. **C 源 `x/y` 是原图像素，不是 common WCS**  
   因此 UI 只锁 RA/Dec，不把它画到当前公共网格；C-P1B-2 才做重投影/共同网格。

---

## 3. 截图

| 文件 | 内容 |
|---|---|
| [gui-chain-app.png](evidence-ego/gui-chain-app.png) | 修复前：锁定 C 源后 note 仍“正在查询” |
| [gui-chain-app-fixed.png](evidence-ego/gui-chain-app-fixed.png) | 修复后：note 显示 `数据不足（insufficient_data）`；C 覆盖卡/源列表/识别卡 |
| [gui-chain-view.png](evidence-ego/gui-chain-view.png) | 视图深链 Barnard、10 帧/5 曝光/3 夜 |

---

## 4. 结论

- **主链路已打通**：app 分析 → 检测源/目标标记 → C 覆盖卡 → C 源列表 → 锁定 → `/api/identify` → Gaia known/insufficient。
- **视图深链已打通**：view → MoveScope popup → REAL/L2/S + C 覆盖。
- **发现并修复 1 个 P1 UI 状态 bug**。
- **仍未打通**：C-P1B 发现/取数没有 GUI 按钮；任意天区尚未做到“自动发现 → 自动取数 → 多历元序列”。
- **不是“发现行星”链路**：C 源是检测源，D 只做已知/未知比对，`discovery_claim=false`。

---

## 5. 补充：级联 / 残差页

打开 <http://127.0.0.1:8790/cascade>（默认 Barnard）：

```text
标题：MoveScope · 级联 / 残差检测
统计：4 历元 / 215 L1 / 215 MASKED / 241 RESIDUAL / real
每历元：L1 49–61，masked 49–61，residual 54–68
表格：4 历元 × 3 层 = 12 张表
页脚：L1 6σ ≤80/期 · masked 2px · residual 5σ ≤80/期
免责声明：残差候选 ≠ 新天体；未匹配不等于新星
JS error：无
```

说明：

- L1 已与 A/C 的 `detect_sources(max_sources=80)` 对齐，不再出现 208/期；
- residual 用 5σ + ≤80/期，仍可能有 PSF 翼/边缘噪声，页面明确标 `RESIDUAL_CANDIDATE`；
- 页面不是“自动发现新天体”，是分层审查/复核工具；
- 截图：[cascade-page.png](evidence-ego/cascade-page.png)。

---

## 6. 补充：跨历元轨迹页

打开 <http://127.0.0.1:8790/tracks>（默认 Barnard）：

```text
标题：MoveScope · 跨历元轨迹候选
cascade：L1 215 / residual 241 / 4 历元
D0 ARTIFACT: 45
D1 KNOWN CATALOG: 0（B 当前 fixture partial，D1 不给）
D2 NOT IMPLEMENTED: 0
D3 UNKNOWN MOVING: 10
D4 UNKNOWN STATIONARY: 195
cards: 120（显示上限，计数为全量）
页脚：轨迹候选 ≠ 新天体；D5 人工/后续确认
JS error：无
```

说明：

- D0–D4 是候选分级，不是发现声明；
- 4 期数据下 tracklet 关联有歧义，卡片带 caveat；
- D1=0 是因为 B 返回 `catalog_status=partial`，不是轨迹算法丢 known；
- 截图：[tracks-page.png](evidence-ego/tracks-page.png)。

---

## 7. 三模块联动：View → 分析层 → SQL

View 深链：

```text
http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&target=barnard&t=60920&t0=60890&t1=60950
```

实测：

- 时间窗口 `30` 天：MJD 60890.000–60950.000（UTC 2025-08-03 → 2025-10-02）；
- 页面显示“窗口 = 分析层过滤范围；不是数据时相/当前时刻”；
- 三个入口并存：级联/残差、轨迹发现、MoveScope；
- 点击“进入轨迹发现”（用户手势）→ 新页：
  `http://127.0.0.1:8790/tracks?ra=269.452077&dec=4.693365&fov=0.5&t0=60890.0000&t1=60950.0000&from=view`
- 分析页显示：
  `来自视图 / 时间窗口 MJD 60890.000–60950.000 / 保留 3/4 期`
  cascade：L1 172 / residual 194；
  D0 51 / D1 0 / D2 0 / D3 2 / D4 203；
- 无 JS error；
- 编程式 `evaluate(click)` 会被浏览器拦 popup，页面 toast 提示“已复制链接”；真实用户点击正常打开。

SQL 层：

```text
GET /api/v1/epochs?target_id=barnard&mjd_min=60916&mjd_max=60923
→ count=2, time_filter.applied=true
```

结论：View 选区/时间 → App 时间过滤/cascade/tracks → DB epochs/coverage 链路打通；时间窗口过小时 app 返回空/insufficient，不伪造。

截图：[three-module-view.png](evidence-ego/three-module-view.png)、[three-module-tracks.png](evidence-ego/three-module-tracks.png)。

---

## 8. 模式化重构：View 单入口 + MoveScope 同画布

### View

- 只剩“在 MoveScope 打开”一个分析入口；
- `#btn-cascade` / `#btn-tracks` 已删除；
- handoff：
  `http://127.0.0.1:8790/?ra=269.452077&dec=4.693365&fov=0.5&t0=60890&t1=60950&from=view`
- 页面文案：进入 MoveScope 后切换“普通 / 级联-残差 / 轨迹”。

### MoveScope

- 主界面模式栏：`普通 | 级联/残差 | 轨迹`，URL 始终 `/`，不跳 `/cascade` 或 `/tracks`；
- 普通：原分析页；
- 级联：同一画布出现 L1 实心 / masked 灰叉 / residual 空心琥珀，图层开关；
  当前帧 `57/57/66`；时间窗口 `保留 3/4 期`；
- 轨迹：同一画布出现轨迹线/节点，侧栏 `D0 51 / D1 0 / D2 0 / D3 2 / D4 203 / 共 256`；
- 点击 residual/节点仍走 `/api/identify`；
- 无 JS error；
- 旧 `/cascade`、`/tracks` 保留为“详细表格”诊断页。

截图：[mode-view-single-entry.png](evidence-ego/mode-view-single-entry.png)、
[mode-cascade.png](evidence-ego/mode-cascade.png)、
[mode-tracks.png](evidence-ego/mode-tracks.png)。

---

## 9. 时间选择 / 播放 / 时间轴优化

### 时间选择

- 新增 UTC 输入 `YYYY-MM-DD HH:MM` + “定位”；
- 新增 `−1d / −1h / +1h / +1d`；
- 输入 `2025-09-02 00:00`：
  - 时钟 `2025-09-02 00:00:00 UTC`
  - MJD `60920.0000`
  - 窗口 `MJD 60555.000 – 61285.000`
  - URL 同步 `t/t0/t1`
- 所有入口统一走 `commitTime`；`state.mjd` / `globe.setClock` 各只有一处写入。

### 时间轴

5 个 UTC 刻度：

```text
2025-04-24 / 2025-09-03 / 2026-01-13 / 2026-05-24 / 2026-10-03
```

轴左标 `UTC 时间轴`；窗口仍单独显示。

### 播放

- 播放改为**观测序列**，不是连续时间；
- 步长 0.5×=2.4s / 1×=1.2s / 2×=0.6s；
- 地球 700ms 最短路径 tween，不再 216°/s 疯转；
- 末尾停止、不循环；
- 默认打开在最后一期，按播放会从第一个观测开始并提示：
  `已从第一个观测开始播放`。

实测：

```text
默认打开：MJD 61316.5299（最后）
按播放 450ms：MJD 60789.7508（第一个观测）
toast：已从第一个观测开始播放
mode：观测序列
JS error：无
```

截图：[time-axis-new.png](evidence-ego/time-axis-new.png)、[time-playing-new.png](evidence-ego/time-playing-new.png)、[time-default-play.png](evidence-ego/time-default-play.png)。
