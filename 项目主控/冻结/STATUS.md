# T3 离线冻结器 · 状态：PASS

> 冻结：2026-11-14 23:52 CET · 迁移后复核：2026-11-15 00:04 CET
> 目录：`项目主控/冻结/`
> 快照：`项目主控/冻结/demo/snapshot-2026-11-14/`（复核后未重新 freeze）

## 验收结果（冻结时）

| 项 | 结果 |
|---|---|
| 快照文件 | 27/27，sha256 全匹配 |
| 5 目标离线 | **5/5 PASS**，与在线一致 |
| 2 个自定义坐标 | 4/8 期，匹配 barnard/epsind_b |
| 路径安全 | 无绝对路径、无 `..`、无符号链接 |
| 缺文件检测 | 删任一 NPZ → verify 退出 1，run_offline 退出 2 |
| 可移植性 | 拷到 `/tmp` 后 verify/run 仍 PASS |

## 结果表

| target | mode | n | readiness | class | μ (mas/yr) |
|---|---|---|---|---|---|
| barnard | real | 4 | L2 | S | 8032.88 |
| epsind_b | real | 8 | L3 | L | 4706.66 |
| wise0855 | real | 8 | L0 | U | — |
| rw_aur | hybrid | 8 | L2 | V | 49.33 |
| r_dra | hybrid | 8 | L2 | V | 794.94 |

## 2026-11-15 00:04 迁移后复核

在 `tool/t3-freeze` worktree 上按 DoD 重跑，**快照文件未改动**：

| 命令 | 结果 |
|---|---|
| `verify.py --snapshot ... --strict` | **PASS**：27/27 文件 sha256 匹配，checksums 27 条一致，extras=0，missing=0，warnings=0，退出码 0 |
| `run_offline.py --snapshot ... --targets all` | **PASS 5/5**：`urlopen` 已阻断、`attempts=0`、20 个缓存键全部命中快照，退出码 0 |
| `run_offline.py --snapshot ... --targets all --coords` | **PASS 7/7**（5 目标 + 2 坐标），退出码 0 |

关键输出：

```text
verify:
  files    : 27/27 ok · 3.3 MiB
  checksums: 27 entries · ok
  extras   : 0
  missing  : 0
  warnings : 0
RESULT: PASS (strict)

run_offline:
  offline  : urllib.request.urlopen blocked · attempts=0 · cache=snapshot/epochs (20 keys)
  app      : manifest aef1a55bb4d4… · current 97051c219336…  [DRIFT]

barnard          real     4  L2        S         8032.88     34.24  snapshot_results PASS
epsind_b         real     8  L3        L         4706.66    129.62  snapshot_results PASS
wise0855         real     8  L0        U            0.00         —  snapshot_results PASS
rw_aur           hybrid   8  L2        V           49.33     77.18  snapshot_results PASS
r_dra            hybrid   8  L2        V          794.94     58.01  snapshot_results PASS

RESULT: PASS (5/5)
```

## 指纹漂移说明（迁移导致，非科学逻辑变化）

- manifest `app_fingerprint` = `aef1a55b…`：冻结于迁移前，路径前缀为 `app/`。
- 当前 `app_fingerprint` = `97051c21…`：迁移后路径前缀为 `系统/app/`。
- 逐文件核对：12/12 个 app 关键源码/数据文件的内容 sha256 **完全相同**，差异只在路径前缀；
  因此 `[DRIFT]` 是迁移告警，不是科学管线漂移。
- 结论：**不重新 freeze**。`run_offline` 结果与快照内 `results/*.json` 逐项一致；
  只有内容哈希变化或结果不一致时才按提示重新 freeze。

## 负向测试（临时副本，未改快照）

| 场景 | verify | run_offline |
|---|---|---|
| 删除一个 NPZ | FAIL，退出 1（`缺文件`） | 预检失败，退出 2 |
| 翻转 NPZ 一个字节 | FAIL，退出 1（`sha256 不符`） | 预检失败，退出 2 |
| 增加未登记文件 | `--strict` FAIL 退出 1；默认 PASS + warning | — |
| 缺少 `manifest.json` | 退出 2 | 退出 2 |
| 复制到 `/tmp` 后重跑 | PASS (strict) | PASS 5/5 |

## 注意事项

- 迁移后重跑时，`app 指纹`与冻结时不同（manifest `aef1a55b…` vs 当前 `97051c21…`），但结果仍 5/5 一致；这是路径迁移，不是科学逻辑改动。
- RW Aur / R Dra 是 `hybrid_no_pixels`：冻结的是模拟输入，不是把 SIM 标成 REAL。
- 快照不包含整幅 FITS，只含 NPZ + 支撑文件。

## 复验

```bash
cd /Users/yiao/Documents/huellaX/项目主控/_worktrees/tool-t3-freeze
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY 项目主控/冻结/verify.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --strict
$PY 项目主控/冻结/run_offline.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --targets all
```
