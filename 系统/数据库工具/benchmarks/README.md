# 基准报告

| 文件 | 数据 | 生成方式 |
|---|---|---|
| `report.json` | 生产库 5,857 帧（真实元数据） | `python3 cli.py bench --runs 200` |
| `scale_report.json` | 200,000 帧（**合成**，仅用于规模测试） | `tools/make_scale_fixture.py --rows 200000` + `cli.py bench` |
| `scale_report_1m.json` | 1,000,000 帧（**合成**，仅用于规模测试） | `tools/make_scale_fixture.py --rows 1000000` + `cli.py bench` |

合成规模库写入 `var/bench_scale*.sqlite`，与生产库 `var/catalog.sqlite` 完全隔离；
生成脚本在文件头与输出里都明确标注“合成数据，不是真实观测”。

`cases[].p95_ms` 是单机、只读 SQLite、页缓存预热后的查询延迟，**不含** HTTP 序列化与网络。
`last_count` 是该 case 最后一次查询返回的帧数，用来判断“快”是不是因为“没命中”。

复现：

```bash
cd 数据库工具
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY cli.py bench --runs 200 --out benchmarks/report.json

$PY tools/make_scale_fixture.py --rows 200000 --db var/bench_scale.sqlite
$PY cli.py bench --db var/bench_scale.sqlite --runs 200 --out benchmarks/scale_report.json

$PY tools/make_scale_fixture.py --rows 1000000 --db var/bench_scale_1m.sqlite
$PY cli.py bench --db var/bench_scale_1m.sqlite --runs 200 --out benchmarks/scale_report_1m.json
```

跑完可以删除 `var/bench_scale*.sqlite` 释放约 700 MB。
