# SPHEREx 天区视图 · MoveScope（视图工具）

把数据库里的 **5,893 帧 / 5,562 次 SPHEREx 曝光**变成一颗可转动的三维地球和一个天区选择器：
在地球上点一下，就知道此刻那块天在谁的头顶；反过来，在天图上圈一块天区，地球会标出"该天区正上方"的站点，
并用真实曝光索引回答"**本地**拍过没有、拍了几帧几夜"。

![预览](docs/preview.png)

---

## 1. 运行

```bash
cd 系统/视图工具
./start.sh            # 默认 http://127.0.0.1:8830/
./start.sh 8831       # 换端口
```

- 端口固定 **8830**（MoveScope 8790、数据库 8797），**不自动扫端口**；被占用时直接报错退出。
- 页面用 ES module + `fetch`，必须走 HTTP；直接双击 `index.html` 会被浏览器拦掉。
- 无构建、无网络依赖：three.js 与地球纹理都在 `vendor/`、`assets/`。
- 需要 WebGL2；不支持时页面会明确提示"浏览器不支持 WebGL2"，而不是让你去启动服务。

## 2. 深链契约（T1）

```text
http://127.0.0.1:8830/?ra=269.452077&dec=4.693365&fov=0.5&target=barnard
http://127.0.0.1:8830/?target=epsind_b          # 只给目标，用预设坐标
http://127.0.0.1:8830/?ra=120.5&dec=12.25&fov=0.4&t=61300.2
```

| 参数 | 含义 | 缺省 |
|---|---|---|
| `ra` / `dec` | 天区中心（度，ICRS） | 巴纳德星 |
| `fov` | 视场（度，0.05–180） | 3.5（默认）/ 目标预设 |
| `target` | 目标 id（`barnard`、`epsind_b`、`wise0855`、`rw_aur`、`r_dra`、`m101`、`campo_a`） | — |
| `t` | 时相（MJD） | 本地索引最后一次曝光 |

- **解析顺序**：`location.search` 优先，`location.hash` 兜底（旧的 `#ra=…` 链接仍可用）。
- 任何参数非法/缺失都回退默认，不抛错。
- 页面状态会以 `history.replaceState` **写回 search**（含 `target`、`t`），所以"复制链接"一定可复现。

## 3. 在 MoveScope 打开（T2）

按钮打开 `http://127.0.0.1:8790/?target=<id>&ra=<预设原值>&dec=<预设原值>&fov=<fov>`。

- **命中判定**：当前天区与 7 个目标之一的角距 ≤ **1 角秒**才算"就是它"；命中时带 `target` 并**使用预设的原始坐标**（不四舍五入），MoveScope 的 `sameAsPreset` 要求 `|Δ| < 1e-6` 才不会掉进 `custom → synthetic`。
- **探活**：`GET /api/health`（`mode:'no-cors'`）；不启动 MoveScope 就复制链接并提示"先 `app/server.py`"，不会假装打开。
- **地址**：`MOVESCOPE_ORIGIN` 默认 `http://127.0.0.1:8790`；可在页面加载前用 `window.MOVESCOPE_ORIGIN` 覆盖（联调/换端口时不必改源码）。
- **未命中**：只传自定义 `ra/dec/fov`，面板上明确写"该天区会走 **SIM** 路径"。在 T2 数据桥接完成前，这是事实，不是免责声明。

实测（Barnard，fov 0.5；MoveScope 临时副本 :8791，避免占用保留的 8790）：MoveScope 徽章 **REAL 真实**、**L2 就绪度**、**4 期**、判定 **S 静止**。

## 4. "本地命中"口径（T5）

**2.47°** = 单片 3.49° 方视场的半对角线。中心落在 2.47° 内，才算"这帧拍到了该天区"。
统计拆三层：

| 指标 | 含义 |
|---|---|
| **帧** | 满足条件的探测器帧数（一行 = 一个探测器） |
| **曝光** | 不同 `obsid` 数（一个 obsid = 一次曝光，最多 6 帧） |
| **夜** | 不同 UTC 日期数 |

按这个口径（本地索引内）：

| 目标 | 帧 | 曝光 | 夜 | 最近中心距 |
|---|---|---|---|---|
| Barnard's Star | 10 | 5 | 3 | 13.7′ |
| Epsilon Indi B | 27 | 14 | 7 | 28.1′ |
| WISE 0855 | 26 | 12 | 6 | 56.3′ |
| RW Aurigae | 8 | 7 | 3 | 110.6′ |
| R Draconis | 11 | 8 | 6 | 49.3′ |
| M101 | 10 | 5 | 2 | 68.7′ |
| 场 A | 9 | 5 | 4 | 0.1′ |

> 快照：2026-11-15 00:10 CET，数据库 `frames` 5,893 行 / 5,562 次曝光 / 23 个观测周；本表由 `tools/smoke.py` 按 `fields.json` 与 2.47° 口径复核。

- 0 命中时显示"本地索引在 2.47° 内无命中"，并说明**本地只是 23 个观测周的子集**，不写成"没被拍过"。
- 命中目标时额外显示 **策展历元**（数据库 `target_epochs` 去重后的曝光数：Barnard 4、Eps Ind 10、RW Aur 1），点一下直接跳到该历元的时刻。

## 5. 数据来源与重建（T3/T4）

```
系统/数据库工具/var/catalog.sqlite : frames        ──build_field_index.py──▶  系统/视图工具/data/fields.json
系统/app/data/targets.json                          ──sync_targets.py──────▶  系统/视图工具/data/targets.json
系统/数据库工具/var/exports/epoch_catalog.json      ──sync_targets.py──────▶  系统/视图工具/data/epoch_index.json
```

```bash
# 重建曝光索引（默认读数据库；已存在则拒绝，加 --force 覆盖）
python3 tools/build_field_index.py --check     # 只比对
python3 tools/build_field_index.py --force

# 从 DB 导出物重建到临时文件（验证不依赖 header_cache / 已删除目录）
python3 tools/build_field_index.py --source export --out /tmp/fields.json --force

# 重建目标表 + 策展历元（单一源：app/data/targets.json）
python3 tools/sync_targets.py --check
python3 tools/sync_targets.py
```

- `fields.json` 不再依赖已删除的 `app/data/header_cache`；`--source export|db|headers` 可选，默认 `db`。
- `--out` 支持绝对路径或相对路径（相对当前工作目录）；默认 `data/fields.json`，已存在时拒绝覆盖，需加 `--force`。
- 数据快照随数据库变化：重建后行数会跟着 `frames` 表走（当前 5,893 帧 / 5,562 曝光），`--check` 一旦失败说明源变了，重跑即可。
- 输出带 `generated_by / source / source_sha256 / generated_at / count / exposures`。
- `data/targets.json` 是脚本生成的副本（含 `coord_unc_arcsec`，`rw_aur.truth` = 12.0 d / 0.85 mag），**不再手工维护第二份**。
- 重建结果与数据库逐行一致（`--check` 退出码 0）；`--source export` 与 `db` 当前逐行相同。

## 6. 自检

```bash
python3 tools/smoke.py
```

一条命令检查：DOM id、fields 行数/来源与数据库一致、targets/epoch_index 与单一源一致、交接契约
（`MOVESCOPE_ORIGIN` 默认 8790 + GET + 全精度 + `target`）、2.47° 覆盖表与 README 一致
（RW Aurigae 非 0）、`fields` 可由 DB/export 重建、文案不含旧项目名/旧端口/过时提示、
`node --check`、`start.sh` 端口。全部通过退出码 0。

## 7. 边界

1. **不做 FITS 渲染**：多期图像/闪烁/相减是 MoveScope 的职责，这里只选天区。
2. **本地索引是子集**：5,893 帧只覆盖 23 个观测周（2025-04-24 → 2026-10-03），空缺不代表没有观测。
3. **自定义天区仍走 SIM**：等 MoveScope 的 T2 数据桥接完成后，这里传过去的 `ra/dec/fov` 才有真实历元。
4. 命中判据是"中心距 ≤ 2.47°"，不是逐像素求交；对大倾角扫描带是保守估计。
5. 地球纹理 2048 px，2 倍屏近看会软。

## 8. 文件结构

```
系统/视图工具/
├── index.html              单页
├── css/style.css           HUD 样式
├── js/
│   ├── main.js             状态、深链、交接、覆盖口径
│   ├── globe.js            three.js 地球
│   ├── skymap.js           天图选择器
│   └── astro.js            时间 / 坐标 / 太阳
├── data/
│   ├── fields.json         5,893 帧 / 5,562 曝光（生成物）
│   ├── targets.json        7 目标（生成物）
│   └── epoch_index.json    37 个策展历元（生成物）
├── tools/
│   ├── build_field_index.py
│   ├── sync_targets.py
│   └── smoke.py
├── docs/                   预览截图与交接修复报告
├── vendor/three.module.js
├── assets/earth_*.{jpg,png}
└── start.sh
```

## 9. 参考与许可

- 视觉与交互参考 [bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view)；未复用其代码或数据，three.js 自绘、离线可用。
- three.js r160（MIT）；地球纹理来自 NASA Visible Earth（公有领域），取自 three.js examples。
- SPHEREx 数据：NASA/IPAC IRSA 公开数据。
