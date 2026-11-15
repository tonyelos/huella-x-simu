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
