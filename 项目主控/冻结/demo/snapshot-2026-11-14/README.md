# T3 离线演示快照

- 生成时间：`2026-11-14T22:46:31Z`
- schema：`1.0`
- FOV：`0.25` 度
- app 指纹：`aef1a55bb4d44dbc9caf5a207e9d4fc23f978ba20ecad41feb1c952847f30395`
- Python：`3.12.14` · NumPy：`2.3.5`

## 这是什么

这是 MoveScope 5 个官方目标（以及冻结时指定的自定义坐标）所需的 **SPHEREx NPZ 切片副本** +
逐文件 sha256 清单 + 冻结时的黄金结果。目标是在断网、且不依赖 `app/data/epochcache` 的情况下
复现同一组 `mode / readiness / class / μ`。

**不是**原始 FITS 存档，**不含** EPSF/整幅 65 MB 曝光，也**不生成**新历元。
RW Aur / R Dra 没有真实 NPZ，按 HYBRID（模拟时序）冻结输入参数，因此不占像素文件。

## 摘要

- 目标：5（ok 3 / hybrid 2）
- 自定义坐标：2
- 真实历元文件：20
- 结果文件：5
- 文件总数：27（3.3 MiB）
- 缺失：0

## 校验

```bash
python3 项目主控/冻结/verify.py --snapshot . --strict
# 或直接：
shasum -a 256 -c checksums.txt
```

## 断网运行 5 目标

```bash
python3 项目主控/冻结/run_offline.py --snapshot . --targets all
```

`run_offline.py` 会把 `urllib.request.urlopen` 替换成抛错函数，并把 `_cache_path` 重定向到
本快照的 `epochs/`；若快照缺文件，会直接报错退出（退出码 2），不会静默退回模拟数据。

## 目标

| id | mode | status | 历元 | 文件 |
|---|---|---|---|---|
| `barnard` | real | ok | 4 | results/barnard.json |
| `epsind_b` | real | ok | 8 | results/epsind_b.json |
| `wise0855` | real | ok | 8 | results/wise0855.json |
| `rw_aur` | hybrid | hybrid_no_pixels | 0 | results/rw_aur.json |
| `r_dra` | hybrid | hybrid_no_pixels | 0 | results/r_dra.json |

## 自定义坐标

| ra | dec | resolver | matched | sep(arcsec) | status | 历元 |
|---|---|---|---|---|---|---|
| 269.452077 | 4.693365 | epoch_source | barnard | 0.0 | ok | 4 |
| 331.044147 | -56.782844 | epoch_source | epsind_b | 0.0 | ok | 8 |

## 已知限制

- RW Aur / R Dra 没有真实像素：`mode=hybrid`、`status=hybrid_no_pixels`，只冻结模拟输入（种子/时间轴/PSF/背景）。
- 任意未被本地时代目录覆盖的坐标会得到 `no_coverage`；当前自定义坐标用“最近目标”退化解析（`resolver=fallback`）。
- 缓存键依赖冻结时的 `app/core/data.py::_cache_path`；换 app 版本后请重新 freeze，不能把旧快照当新缓存。
- 快照只覆盖 NPZ 切片与少量模拟支撑文件（PSF、背景），不含 FITS/EPSF，不能用于重新测光或改 WCS。
- `run_offline.py` 校验的是结果一致性（class/readiness 严格，μ ±5%、PA ±5°），不是像素级 bit 对比。

## manifest 关键字段

- `targets[].epochs[]`：`obsid/detector/release/version/mjd/key/slice_url/cache_key/file/sha256/bytes/status`。
- `status`：`ok | missing | fetch_failed | no_coverage | hybrid_no_pixels`。
- `manifest.app_fingerprint`：冻结时 app 关键源码/数据 sha256；换版本后结果不可比。
- `coords[]`：输入坐标、resolver、匹配目标、匹配角距；epochs 复用匹配目标的冻结文件。
