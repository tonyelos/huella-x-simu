# T3 · 离线演示冻结器（Offline Demo Freezer）

把 MoveScope 5 个官方目标（以及指定自定义坐标）所需的 SPHEREx NPZ 切片冻结成
**自包含、带 sha256 清单、可移植**的快照；断网也能复现同一组
`mode / readiness / class / μ / PA`。

- **只读** `系统/app/data/epochcache/`：默认只复制已有 NPZ，不删除/覆盖任何缓存。
- **不伪造数据**：缺失就记 `missing`；显式 `--fetch` 也补采失败就记 `fetch_failed`。
- **不改 app 科学管线**：只 import `系统/app/core`，不启动服务也能 freeze/verify。
- 只依赖 **stdlib + numpy**（读 NPZ 用 numpy）。

## 目录

| 文件 | 作用 |
|---|---|
| `freeze.py` | 解析历元 → 复制/补采 NPZ → 生成 `manifest.json` + `checksums.txt` + `results/*.json` + `README.md` |
| `verify.py` | 重算 sha256、检查缺失/损坏/多余文件、路径安全、历元数、checksums 一致性 |
| `run_offline.py` | 阻断 `urllib.request.urlopen`，把 `_cache_path` 重定向到快照，跑 5 目标并与黄金结果对比 |
| `common.py` | 共享的路径/哈希/清单工具 |
| `demo/snapshot-<date>/` | 冻结产物（可整体拷走） |

## 快速开始

在项目根目录 `/Users/yiao/Documents/huellaX`：

```bash
PY=python3   # 本机可用 /Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

# 1) 冻结（已有缓存；不联网）
$PY 项目主控/冻结/freeze.py --targets all \
  --coords "269.452077,4.693365;331.044147,-56.782844" \
  --fov 0.25 --out 项目主控/冻结/demo/snapshot-2026-11-14

# 2) 校验（严格：missing/fetch_failed 也失败）
$PY 项目主控/冻结/verify.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --strict

# 3) 断网复跑 5 目标
$PY 项目主控/冻结/run_offline.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --targets all
```

`--out` 默认 `demo/snapshot-<本地日期>`；已存在时默认拒绝覆盖，确认后加 `--force`
（只允许删除带本工具 `manifest.json` 的目录）。

### 常用参数

| 参数 | 说明 |
|---|---|
| `--targets all` | 5 个 `official=true` 目标；也可 `barnard,epsind_b` |
| `--coords "ra,dec;ra,dec"` | 自定义坐标；优先用 T2 `epoch_source.resolve_epochs`，不可用时退化为 `epoch_catalog` 最近目标（`resolver=fallback`） |
| `--no-resolver` | 跳过 T2 resolver，自定义坐标直接走最近目标 fallback（离线冻结更可控） |
| `--fov 0.25` | 切片边长（度）。**演示用 0.25**：现有 0.25 缓存对 3 个 real 目标全覆盖 |
| `--fetch` | 缓存缺失时联网补采；写入临时暂存目录后复制进快照，**不写 app 缓存** |
| `--no-results` | 不调用 `/api/analyze`；`run_offline` 会退回内置黄金表 |
| `--api` | MoveScope 服务地址，默认 `http://127.0.0.1:8790`；服务不在跑时 results 记 `skipped` |

## 缓存键

冻结/离线运行都调用 `系统/app/core/data.py::_cache_path(ep, ra, dec, size)` 计算键，
**不自己拼文件名**。manifest 里保存 `cache_key`（basename）与相对 `file`：

- app 改了缓存键（例如加入 `version`）→ 重新 freeze 即可；旧快照不能当新缓存。
- `run_offline` 用 app 当前的 `_cache_path` 算键再查快照；查不到直接报错并退出 2，
  **不会静默退回 HYBRID/SIM**。

## 快照结构

```text
snapshot-2026-11-14/
  manifest.json          # 目标/坐标/逐历元文件/sha256/bytes/source URL/status
  checksums.txt          # "sha256  relative/path"，可直接 shasum -a 256 -c
  README.md              # 快照自述（动态生成）
  epochs/<target>/*.npz  # real 目标切片（文件名即 app 缓存键）
  support/psf_d2.npy
  support/backgrounds/campoA.npz   # HYBRID 复现所需
  results/<target>.json  # 冻结时的 API 黄金结果（class/readiness/μ/PA）
```

manifest 主要字段：

- `schema_version` / `generated_at` / `app_git` / `app_fingerprint` / `python` / `numpy` / `fov`
- `targets[]`：`id/ra/dec/mode/status/n_epochs/epochs[]/simulation/results_file`
- `targets[].epochs[]`：`obsid/detector/release/version/week/mjd/date_obs/key/slice_url/cache_key/file/sha256/bytes/status`
- `status ∈ ok | missing | fetch_failed | no_coverage | hybrid_no_pixels`
- `coords[]`：输入坐标 + `resolver`（`epoch_source` / `fallback`）+ `resolver_detail` + `matched_target`
  + `match_sep_arcsec` + `resolver_cache_keys_match` + 复用文件
- `missing[]` / `warnings[]` / `summary`（目标/历元/文件/bytes）
- **manifest 内不写绝对路径**；`verify` 会递归检查绝对路径与 `..` 路径段。

## verify 判定

- 缺文件、大小不符、sha256 不符、符号链接、checksums 与 manifest 不一致 → **失败**。
- `--strict` 额外把 `missing / fetch_failed`、real 目标不完整、多余文件判失败。
- 默认把状态类问题记 warning（哈希仍严格）。
- 输出人读摘要；`--json` / `--json-out` 输出机读报告。
- 退出码：0 通过，1 失败，2 快照不可读。

## run_offline 判定

- 预检 manifest 中所有文件（sha256/bytes）后才开始跑；缺文件 → 退出 2，列明缺哪个。
- 把 `urllib.request.urlopen` 替换为抛错函数；运行结束检查 **网络尝试次数必须为 0**。
- real 目标：`build_dataset` 必须返回 `real` 且历元数/obsid/mjd 与 manifest 一致；
  不一致视为“快照不完整/缓存键漂移”，退出 2。
- hybrid 目标：校验模拟时间轴与 **图像栈 sha256**（冻结时记录），防止 app 版本漂移。
- 黄金对比容差：`class` / `readiness` / `mode` 严格，`μ` ±5%，`PA` ±5°。
- 退出码：0 全过，1 结果不一致，2 环境/快照错误。

## 可移植性

快照目录不含绝对路径，也不依赖 `系统/app/data/epochcache` 的文件名之外的信息。
复制到 `/tmp/demo-copy`（甚至换 cwd）后：

```bash
cd /tmp
$PY 项目主控/冻结/verify.py --snapshot /tmp/demo-copy --strict
$PY 项目主控/冻结/run_offline.py --snapshot /tmp/demo-copy --targets all
```

唯一环境要求：同 Python 3.12 + numpy，且 `系统/app/core` 仍能 import（科学管线在 app 里，
不在快照里；本工具冻结的是**数据与结果基线**，不是整个 app 发行包）。

## 已知限制

- **RW Aur / R Dra 没有真实 NPZ**：`mode=hybrid`、`status=hybrid_no_pixels`；冻结的是
  模拟输入（种子/时间轴/PSF/背景），不是“把 SIM 标成 REAL”。结果仍为 V。
- 自定义坐标优先用 T2 `epoch_source`（当前为 `db_legacy`）；resolver 解析出的缓存键必须与
  已冻结目标文件一致才复用，否则明确退回 `epoch_catalog` 最近目标（`resolver=fallback`）。
  未覆盖天区明确 `no_coverage`，不会拿别处的图冒充。
- 不冻结整幅 65 MB FITS / EPSF；只冻结 NPZ 切片与 110 KB 模拟支撑文件。
- 快照与 app 指纹绑定：`manifest.app_fingerprint` 变了，结果可能漂移，演示前重新 freeze。
  **例外（2026-11-15 迁移后）**：`app/…` → `系统/app/…` 的路径迁移也会改变聚合指纹，
  但 12 个 app 关键文件的**内容 sha256 全部相同**；`run_offline` 的 `[DRIFT]` 是预期告警。
  只有文件内容或离线结果变化时才需要重新 freeze（见 `STATUS.md`）。
- `run_offline` 验证的是结果级一致（class/readiness/μ/PA），不是像素级 bit 对比。
- 服务不在跑时 `results/*.json` 会 `skipped`；`run_offline` 退回 `BUILTIN_GOLDEN`（审计基线）。

## 回滚

本工具只在 `项目主控/冻结/` 与显式 `--out` 下写文件；不修改 `系统/app/`、`系统/数据库工具/`、`系统/视图工具/`、`系统/app/data/epochcache/`。
回滚 = 删除生成的快照目录与脚本改动即可；没有数据库/服务状态需要恢复。
