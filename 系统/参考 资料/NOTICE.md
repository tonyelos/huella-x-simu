# 参考资料 · 第三方材料声明

> 本目录是**参考资料**，不是本项目原创代码；**不按 Apache-2.0 再许可**。
> 它们仅用于赛道对齐、数据访问说明与验收；引用来源见下。完整清单位于根目录 `THIRD_PARTY_NOTICES.md`。

| 文件 | 性质 | 来源/权利 |
|---|---|---|
| `01_挑战原文.md` | NASA Space Apps 官方赛道文本（英文原文 + 中文要点） | 官方页面 <https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/>；版权归 NASA / Space Apps；仅作合规参考与引用，不按 Apache-2.0 再许可 |
| `02_数据资源清单.md` | 官方 Resources 清单 + 本项目加工判断 | 资源链接为第三方；本项目加工部分可按 Apache-2.0，但为清晰起见整体按“参考/引用”处理 |
| `03_SPHEREx数据访问笔记.md` | 本项目实测笔记 | 本项目原创（Apache-2.0）；其中 URL/服务名属于事实信息 |
| `04_测试目标天体.md/.csv` | 官方 5 目标 + SIMBAD 坐标 | 坐标来自 SIMBAD/CDS；引用 Wenger et al. (2000), A&AS 143, 9, <https://doi.org/10.1051/aas:2000332> |
| `05_与HUELLA_X的对照.md` | 历史项目对照笔记（旧项目结构） | 本项目原创分析；旧项目名/结构仅作参考；本工作区不含其代码 |
| `README.md` | 资料包说明 | 本项目原创（Apache-2.0） |
| `muestras/*.fits` | 真实 SPHEREx 切片 | NASA/IPAC IRSA；公开数据；引用 <https://irsa.ipac.caltech.edu/Missions/spherex.html>；不暗示 NASA 背书 |
| `muestras/cabeceras.txt` | FITS 头摘录 | 由 NASA 数据派生；NASA 数据条款 |
| `muestras/manifest.json` | 样本清单 | 本项目生成；包含 NASA 数据的 sha256/来源 URL |
| `muestras/LEEME.md` | 样本说明 | 本项目原创（Apache-2.0） |

## 使用边界

1. 官方挑战文本仅用于理解题目与合规引用，**不**作为本项目原创内容对外再许可。
2. NASA 数据/影像按 NASA 公开数据/媒体条款使用；NASA 不背书本项目。
3. SIMBAD 坐标按 CDS 引用要求标注；坐标事实数据不受版权保护，但仍需引用。
4. 若将本目录内容单独再分发，请保留本声明与原始来源链接。
5. 完整第三方清单、许可证文本位置：根目录 `THIRD_PARTY_NOTICES.md`、`NOTICE`、`LICENSE`。
