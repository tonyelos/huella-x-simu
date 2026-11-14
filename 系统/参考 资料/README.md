# AstroNova 参考案例 · Planet X and SPHEREx

NASA Space Apps Challenge 2026 官方赛道 **Planet X and SPHEREx** 的参考资料包。
采集时间：2026-11-14 21:56–22:15（Europe/Madrid）。采集来源：赛道官方页面
<https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/>。

## 这个包里有什么

| 文件 | 内容 | 用途 |
|---|---|---|
| `01_挑战原文.md` | 官方赛道全文（英文原文）+ 中文要点 | 对齐评分点，避免偏离题意 |
| `02_数据资源清单.md` | 页面列出的 8 个数据资源，逐条说明能拿来做什么 | 选型、写文档引用 |
| `03_SPHEREx数据访问笔记.md` | 实测过的访问路径、URL 规则、代码片段、踩坑 | 工程落地（最有用的一篇） |
| `04_测试目标天体.md` / `.csv` | 官方指定的 5 个测试天体 + 坐标 | 验收用例 |
| `05_与HUELLA_X的对照.md` | 赛道要求 → 本项目模块的映射与缺口 | 排期与自检 |
| `muestras/` | 一份真实的 SPHEREx 光谱像切片（FITS）及头信息 | 离线验证解析代码 |

## 实测状态（重要）

采集当天，IRSA 的**检索类**服务（TAP / SIA / DataLink）全部返回 `502 Proxy Error`；
而**静态取数**路径（`/ibe/data/...fits?center=&size=`）与 **AWS S3 公开桶**都正常。
所以本包里的样本是用静态切片接口实取的真实数据，不是占位文件。

| 服务 | 端点 | 结果 |
|---|---|---|
| SIA v2 | `https://irsa.ipac.caltech.edu/SIA` | ❌ 502 / 超时 |
| TAP sync + async | `https://irsa.ipac.caltech.edu/TAP/sync`、`/TAP/async` | ❌ 502 |
| DataLink | `https://irsa.ipac.caltech.edu/datalink/links/spherex` | ❌ 502 |
| 静态切片 | `https://irsa.ipac.caltech.edu/ibe/data/spherex/...fits?center=..&size=..` | ✅ 200 |
| S3 公开桶 | `https://nasa-irsa-spherex.s3.amazonaws.com/` | ✅ 200（可匿名列举） |
| SIMBAD 镜像 | `https://simbad.harvard.edu/simbad/sim-id` | ✅ 200（CDS 主站当时不可用） |

结论：**取数不受阻，受阻的是"按天区找文件"这一步**。详见 `03_SPHEREx数据访问笔记.md`。

## 样本文件说明

`muestras/` 里的 FITS 是 M101 天区（RA 210.80227, Dec +54.34895）的 SPHEREx QR3 光谱像切片：

```
https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/2026W25_2A/l2b-v27-2026-273/2/level2_2026W25_2A_0279_1D2_spx_l2b-v27-2026-273.fits?center=210.80227,54.34895d&size=0.02
```

- 原始文件约 65 MB；`size=0.02`（度）切片后约 3.95 MB
- 7 个 HDU：PRIMARY / IMAGE / FLAGS / VARIANCE / ZODI / EPSF / WCS-WAVE
- IMAGE 只有 12×12 像素，但 EPSF（8752×441）**不参与切片**，占了 3.86 MB —— 这是做"按需取数"时必须知道的
