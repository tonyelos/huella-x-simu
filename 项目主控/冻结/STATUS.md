# T3 离线冻结器 · 状态：PASS

> 时间：2026-11-14 23:52 CET
> 目录：`项目主控/冻结/`
> 快照：`项目主控/冻结/demo/snapshot-2026-11-14/`

## 验收结果

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

## 注意事项

- 迁移后重跑时，`app 指纹`与冻结时不同（manifest `aef1a55b…` vs 当前 `97051c21…`），但结果仍 5/5 一致；若后续 app 科学逻辑改动，需重新 freeze。
- RW Aur / R Dra 是 `hybrid_no_pixels`：冻结的是模拟输入，不是把 SIM 标成 REAL。
- 快照不包含整幅 FITS，只含 NPZ + 支撑文件。

## 复验

```bash
cd /Users/yiao/Documents/huellaX
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY 项目主控/冻结/verify.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --strict
$PY 项目主控/冻结/run_offline.py --snapshot 项目主控/冻结/demo/snapshot-2026-11-14 --targets all
```
