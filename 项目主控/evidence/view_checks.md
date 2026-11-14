# 视图工具 · 验证记录

快照：2026-11-14 23:07 CET。静态服务已在 :8794 运行；未修改工作区。

## 1. 运行状态

- `/` → 200，6,312 B。
- 9 个资产全部 200：`js/main.js`、`js/globe.js`、`js/skymap.js`、`js/astro.js`、`data/fields.json`（547,033 B）、`data/targets.json`、`css/style.css`、`vendor/three.module.js`（670,681 B）。
- `node --check` 4/4 通过；`main.js` 引用的 DOM id 在 `index.html` 中都存在；importmap 的 `three` 解析到 `vendor/three.module.js`。
- 结论：**页面本身能跑，没有 JS 语法错误或缺资源**。

## 2. 数据

`data/fields.json`：

- 5,857 行 × 10 列：`[obsid, detector, ra, dec, mjd, lat, lon, elon, elat, xposure]`。
- 5,544 个不同 `obsid`；探测器 1–6；15 个观测周。
- MJD 60793.541849–61316.529902 = 2025-04-28 → 2026-10-03。
- 与 `数据库工具/var/catalog.sqlite:frames` **逐行一致**（5,857/5,857，无缺失、无值差）。
- README 写“5,857 次曝光”，实际是 **5,857 个探测器帧 / 5,544 次曝光**（多算 313）。

`data/targets.json`：7 个目标；与 `app/data/targets.json` 相比：

- 全部 7 个缺 `coord_unc_arcsec`；
- `rw_aur.truth.period_d` / `amp_mag` 为 null（app 为 12.0 / 0.85）；
- 其余坐标/truth 一致。

## 3. 覆盖（README 规则：中心距 ≤ 1.745°）

| 目标 | 帧 | 曝光 | 夜数 | 跨度 | 最近中心距 |
|---|---|---|---|---|---|
| Barnard's Star | 6 | 3 | 2 | 6 d | 13.7′ |
| Epsilon Indi B | 20 | 10 | 7 | 361 d | 28.1′ |
| WISE 0855 | 10 | 5 | 2 | 1 d | 56.3′ |
| RW Aurigae | **0** | **0** | 0 | — | **114.3′（1.906°）** |
| R Draconis | 5 | 5 | 4 | 8 d | 62.2′ |

- RW Aur 的最近帧中心在 1.906°，落在 1.745° 外、2.47° 半对角线内；它被显示为“没有覆盖”，但 `app/data/epoch_catalog.json` 里有 1 条真实 qr3 历元（sep 1.843°）。
- 视图工具用 1.745°，`find_epochs.py` 用 <1.9°，数据库用 1.745°，半对角线是 2.47°——**四个阈值**。

## 4. 交接链路（三处同时断）

1. **路径错**：`main.js` 的“在 MoveScope 打开”探测 `../app/index.html`；实际前端是 `app/web/index.html`，由 `app/server.py` 在 `http://127.0.0.1:8790/` 提供。`app/index.html` 不存在。
2. **方法错**：用 `fetch(..., {method:'HEAD'})` 探测；app 的 `BaseHTTPRequestHandler` 只实现 `do_GET`/`do_POST`，HEAD → **501**。即使路径对也失败。
3. **精度错**：跳转参数用 `toFixed(5)`；app 的 `sameAsPreset` 要求 `|Δ| < 1e-6`。5 个官方目标的舍入差都在 1e-6–5e-6，全部被判为 `custom`，于是进入 **synthetic** 分支。

> 三重故障叠加：即使修好路径和方法，坐标精度仍会把用户带到模拟数据。

补充：探测失败后的 toast 写“MoveScope 前端尚未落地（docs P3）”，但 `app/web/` 已经是可用前端（:8790）。这是**过时文案**，不是事实；它会误导用户以为产品没做完。

## 5. URL 契约

- README 写：`index.html?ra=269.452&dec=4.693&fov=0.50`。
- 代码只读 `location.hash`（`main.js` 的 `readHash`）；`location.search` 从未读取。
- 所以 README 的深链**不会生效**，页面会加载默认值（RA 210.80 / Dec 54.35），不是 Barnard。
- 本页面自身用 `#ra=…&dec=…&fov=…&t=…` 保存状态，可以分享；但与文档不一致。

## 6. 可复现性

- `tools/build_field_index.py` 输入：`../app/data/header_cache/*.hdr`（已删除，0 个文件）。
- `SRC` 的 `os.listdir` 在 `try` 外；现在运行会直接 `FileNotFoundError`，且 `OUT_JSON` 硬编码、无 `--out`，会覆盖已发布的 `data/fields.json`。
- `header_cache` 被 `.gitignore` 忽略，是 `find_epochs.py` 的网络缓存；即使重新采，也不保证得到同样的 15 周/5,857 行。
- 结论：**`fields.json` 是孤立的快照，当前无法从仓库重建**。数据库的 `export fields` 可以生成同形数据（已验证 5,857 行逐行一致），但需要人工替换。

## 7. 其他缺陷

- `start.sh` 扫 8790–8829，会与 MoveScope 文档端口 8790 冲突；实测 MoveScope 占 8790，本工具被推到 8794。
- 错误提示硬编码 8770；WebGL 初始化失败被误报为“请启动 HTTP 服务”；无 WebGL2 回退。
- 0 个测试；`targets.json` 重复维护；README 的“5,857 次曝光”口径错误。
- 页面品牌仍写 “MOVESCOPE / HUELLA_X”（HUELLA_X 是上一个项目名）。

## 8. 结论

**保留为可选伴侣，不进验收硬路径。**
- 价值：真实 5,857 帧索引、三维地球、天区/天顶/覆盖命中，是很好的“选天区/讲上下文”界面。
- 现状：数据不可重建、交接三重断裂、无测试、口径错误；不能作为 R1 的解决方案。
- 最小修复：读 search 参数、全精度坐标/target_id、GET 探测、删 targets 副本、统一 2.47°、改端口 8830；否则从演示路径移除。
