# MoveScope · 星动仪

**一个天体运动规律识别工具**：输入天区坐标，取回多期 SPHEREx 近红外光谱像，
测量目标的位置序列与亮度序列，用一组物理模型去拟合，最后给出
**运动指纹**（μ、方向、位移、直线性、加速、振幅、周期、显著性）、**规律判定**、**置信度**与**可证伪条件**。

> 理论设计先于代码：先读 `系统/docs/01_理论设计编排.md`，再看实现。

![MoveScope](系统/docs/img/ui-main.png)

*真实数据：Epsilon Indi B，8 期 SPHEREx 切片、361 天基线；轨迹放大窗显示 4.41″ 的位移。*

---

## 1. 它回答什么问题

| 你会看到 | 它意味着什么 |
|---|---|
| **L 匀速直线** | 近邻恒星/褐矮星的高自行；给出 μ（mas/yr）与方向角 |
| **V 变光** | 位置不动、亮度在变（变星、AGN）。**不会被"两图相减非零"误判成移动** |
| **A 加速/弯曲** | 太阳系天体或双星轨道候选；给出二次项显著性 |
| **S 静止** | 背景恒星/星系；给出 3σ 运动上限 |
| **U 不可判定** | 数据就绪度不足；明确说"不猜" |

每个结论都附带：

- **数据就绪度** L0–L3（1 期 / 2 期 / 3–5 期 / ≥6 期跨 6 个月）
- **模型选择**：M0 静止、M1 变光、M2 匀速、M3 视差、M4 加速 的 BIC 概率
- **证据链**与**可证伪条件**
- **数据来源标识**：REAL / HYBRID / SIM，模拟数据绝不伪装成真实观测

---

## 2. 快速开始

```bash
# 推荐：一条命令启动 DB(8797) + app(8790) + view(8830)
cd /Users/yiao/Documents/huellaX
项目主控/启动/start_all.sh
# 打开 http://127.0.0.1:8790/

# 或手动只起 app（预设目标可跑；任意坐标需要 DB 8797）
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
$PY 系统/app/server.py --port 8790
cd 系统/数据库工具 && $PY cli.py serve --port 8797

# 可选：星表服务（D-MVP；离线 fixture 234 行，足够验证 known/ambiguous）
cd 系统/数据库工具
$PY tools/import_gaia.py --rebuild --offline
$PY catalog_server.py --port 8799

# 可选：C-MVP 本地覆盖骨架（8798；只读本地 catalog.sqlite，不联网）
cd 系统/anyfield && ./start.sh 8798
```

首次分析某个真实目标时，会从 IRSA 静态切片接口取数（每期约 4 MB，带本地缓存）；
后续分析直接读缓存。

**参数边界**：FOV 合法范围 **0.02–3.0°**；非法值返回 400 + 可读错误码
（`INVALID_FOV` / `FOV_TOO_LARGE` / `INVALID_COORDINATE` / `TARGET_COORD_CONFLICT`），不再是 500。

**已知/未知识别（D-MVP）**：锁定检测源后，app 会调用独立星表服务（默认 8799）做 Gaia DR3 匹配，显示“已知恒星 / 多候选 ambiguous / 未收录候选 / 星表不可用”；未匹配不等于新天体，不做发现声明。

**任意天区（C-P1B）**：8798 支持本地 NPZ/FITS ingest（P1A）、`/api/v1/discover` 周索引+64KB 头按需发现、`/api/v1/fetch` 单张 S3/IRSA cutout 取数（P1B）；C-native 源列表经 `/api/v1/sources` 返回。74 条有 header 的历元用真实 WCS 四角足迹，其余仍 `center_approx`；未知天区 503 `INDEX_INCOMPLETE`。多历元重投影/共同网格/series 仍未实现。

**当前数据通路**：5 个预设目标走本地真实历元目录；**任意 RA/Dec 先查数据库 `/api/v1/epochs`**
（本地索引 151 周，其中 23 周有帧、共 5,893 帧 / 5,562 曝光），有 ≥2 期真实历元就走真实切片；本地无覆盖时显式
`NO_LOCAL_COVERAGE` + SIM 演示，DB 不可用时显式 `DB_UNAVAILABLE`，不会伪装成真实观测。
坐标落在某个官方目标 0.05° 内时复用该目标的预设路径，保证“手动输入坐标”与“点预设”结果一致。
「离线样本」模式只用于验证 FITS 解析与渲染，不会对任何目标做测量。

---

## 3. 界面怎么用

1. **左栏**：选 5 个官方目标之一，或输入自定义 RA/Dec/FOV；
   数据模式可选「自动（真实优先）/ 仅真实 / 模拟 / 离线样本」。
   自定义坐标会先查数据库；有本地真实覆盖则出 REAL，无覆盖则明确标 SIM，不会静默伪装。
2. **中栏**：天区视图。四种显示：
   - **闪烁**：逐期播放，像小视频；
   - **叠加**：多期中值合成，提信噪比；
   - **相减**：每期减去中值图，正负残差用蓝/琥珀双色；
   - **轨迹**：把测量到的位置连成轨迹，叠加拟合弦。
   下方时间轴可拖动，空格键播放/暂停。
3. **右栏**：鉴定卡。顶部是规律判定与置信度，下面是公众语言解释、
   运动指纹指标、光变曲线、切平面轨迹、BIC 模型对比、证据链与可证伪条件。

**URL 参数**（可嵌入其他网站）：

```
http://127.0.0.1:8790/?target=barnard&ra=269.452077&dec=4.693365&fov=0.2
```

**研究者模式**（右上角开关）会显示观测配置、注入真值对照与质量标记。

---

## 4. 数据与模式

| 模式 | 来源 | 用途 | 标识 |
|---|---|---|---|
| REAL | IRSA 静态切片实取的多期 level2 FITS | 真实观测演示 | 绿 |
| HYBRID | 真实单期背景/PSF/噪声 + 注入目标 | 目标天区真实历元不足时 | 琥珀 |
| SIM | 由真实 PSF/背景/噪声生成的多期时序 | 算法验证、教学 | 蓝 |
| SAMPLE | 仓库自带的两份离线切片 | 解析/渲染核验 | 灰 |

真实历元的发现**不依赖 TAP/SIA/DataLink**（实测采集时段全部 502）：
用 S3 公开桶做「周指向索引」→ 头部 Range 读取 → 定位候选场 → 静态切片取数。
详见 `系统/docs/02_实现与实测记录.md`。

---

## 5. 验证

- **注入-回收**：5 个官方目标 × 8 个随机种子 = 40 次端到端回收，
  分类正确率 **100%**；Barnard μ 回收比 0.98（6.5σ），WISE 0855 1.14（6.1σ）。
- **反例**：RW Aur / R Dra 均判为 **V 变光**（8/8 种子），位置拟合约 0.5σ；
  专门的"运动 vs 变光"对照里，纯变星 30/30 判 V、纯移动 30/30 判 L、
  "变星+邻星混叠"陷阱补上守卫后 0/30 误判为移动。
- **真实数据（旗舰）**：Epsilon Indi B，8 期真实 SPHEREx 切片、基线 361 天，
  测得 **μ = 4.71 ± 0.55″/yr，PA = 129.6°**（SIMBAD：4.68″/yr，121.8°），
  参考星相对天体测量 10–21 颗、RMS 0.108–0.784 px，就绪度 **L3**。
- **真实数据（饱和案例）**：Barnard's Star 4 期，目标饱和（峰值 755–1782 MJy/sr，核心 NaN），
  位移约 5.3″（真值 5.8″）但质心误差 0.4 px；SPHEREx 实测未测出显著位移，采用 Gaia PM 先验标为 `L (catalog_prior)`，并保留 `SATURATED_SOURCE` 与“未独立测出”提示。
- **弱目标**：WISE 0855 在可用波段只探测到 1 期，判 U；这是坐标歧义/深度问题，已写入限制。
- **单测**：`$PY -m unittest discover -s 系统/app/tests -v`（271 项，覆盖 sample、FOV、quality 合并、
  缓存版本键/损坏恢复、LRU、target 冲突、越界像素、schema 契约）。

报告：`系统/docs/validation_report.json`。

### 星表数据接收接口

B 星表服务支持场级接收（需可写 SQLite）：

```bash
curl -X POST http://127.0.0.1:8799/api/v1/catalog/ingest \
  -H 'Content-Type: application/json' \
  -d '{"catalog":"gaia","ra":158.578,"dec":9.595,"radius_deg":0.25,"mag_limit":20}'
```

app 侧自动重试默认关闭；显式设置：

```bash
export CATALOG_AUTO_INGEST=1
export MOVESCOPE_CATALOG_INGEST_TIMEOUT=60
```

边界：TAP 失败返回 502 且不写部分数据；场级 upsert，不删其它场；不是全量 Gaia。

---

## 6. 目录

```
系统/
  docs/
    01_理论设计编排.md      理论基线（先读这个）
    02_实现与实测记录.md     实现、数据实测、偏差登记
    validation_report.json  注入-回收结果
  app/
    server.py               HTTP 服务 + 图像渲染
    core/
      fitsio.py             最小 FITS 读取器（多块头/BINTABLE）
      wcs.py                TAN-SIP、切平面、LVF 波长
      photometry.py         FLAGS 掩膜、匹配滤波、PSF 质心、孔径测光
      motion.py             模型族、BIC、运动指纹、分类、公众话术
      pipeline.py           探测→测量→相对定标→关联→推断
      simulator.py          基于真实 PSF/背景/噪声的观测模拟器
      data.py               真实/模拟/样本数据适配与缓存
    web/                    Ego 浏览器前端（无构建步骤）
    data/                   目标表、历元目录、周索引、缓存
  数据库工具/                SQLite 元数据目录 + 内容寻址 Blob + 只读 API
  视图工具/                  three.js 天区选择器（可选伴侣）
  参考 资料/                 赛道原文、数据访问笔记、样本 FITS
  tools/
    scan_week_index.py      构建周指向索引
    find_epochs.py          为目标坐标寻找真实历元
    validate_pipeline.py    注入-回收验证
```

项目控制面（不在 `系统/` 内）：

```text
项目主控/
  架构/                   基础架构与迁移记录
  验收/ 冻结/ 启动/        控制面工具
  _归档/                  迁移前快照与未验证中间文件

LICENSE / NOTICE              原创内容 Apache-2.0 + 第三方摘要
THIRD_PARTY_NOTICES.md        第三方材料完整清单（代码/数据/图片/字体）
REFERENCES.md                 提交表单可直接引用的简版引用
CITATION.cff                  引用元数据
```

---

## 7. 边界

- 不产生"发现声明"，只产生**候选 + 证据 + 证伪条件**。
- 6.15″ 像素下，对 μ < 0.5″/yr 的天体只能给上限。
- 饱和源（如 Barnard's Star）的质心精度受 bloom 影响，工具会显式标记 `SATURATED_SOURCE`。
- M5 开普勒轨道 / M6 太阳系二体轨道尚未实现完整拟合；弯曲轨迹先用 M4 的二次项判别。
- 光谱：单张切片只采样很窄的波段；多波段 SED 需要同历元多探测器切片（v1 只输出波段标签）。

---

## 8. 数据与工具引用

- **SPHEREx level2 数据**：NASA/IPAC IRSA，QR3 / DR1；<https://irsa.ipac.caltech.edu/Missions/spherex.html>。
  本地样本/头/切片见 `系统/参考 资料/muestras/`、`系统/app/data/`、`系统/数据库工具/var/`。
- **目标坐标/真值**：SIMBAD/CDS（2026-11-14 查询）；Wenger et al. (2000), A&AS 143, 9,
  <https://doi.org/10.1051/aas:2000332>。
- **3D 引擎**：three.js r160（MIT），`系统/视图工具/vendor/three.module.js`；
  MIT 全文：`系统/视图工具/vendor/three.LICENSE`。
- **地球纹理**：NASA Visible Earth / Blue Marble（NASA 媒体条款），经 three.js r160 examples
  （MIT）分发；`系统/视图工具/assets/earth_*.{jpg,png}`。
- **交互范式参考**：WiseView（Backyard Worlds: Planet 9）<http://byw.tools/wiseview>；
  3D 地球/HUD 视觉参考 [bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view)
  （**未复用其代码/数据**）。
- **赛道**：NASA Space Apps Challenge 2026 · Planet X and SPHEREx，
  <https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/>。

---

## 9. 许可与第三方声明

- **本项目原创代码/文档/截图**：**Apache License 2.0**；全文见 [`LICENSE`](LICENSE)，
  版权与第三方摘要见 [`NOTICE`](NOTICE)。
- **全部第三方代码/数据/图片/参考资料的许可证与来源**：见
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
- **提交表单可直接引用的简版清单**：见 [`REFERENCES.md`](REFERENCES.md)（许可证、数据、图片、参考项目、官方条款）。
- **参考资料**（`系统/参考 资料/`）是第三方材料，**不**按 Apache-2.0 再许可；
  见 [`系统/参考 资料/NOTICE.md`](系统/参考%20资料/NOTICE.md)。
- NASA Space Apps 官方条款：
  <https://www.spaceappschallenge.org/legal/>、<https://www.spaceappschallenge.org/faq/>。
  未指定许可证的原创内容按 Apache-2.0 分发；第三方材料必须无限制可用或 OSI 开源，
  且许可证必须写入提交文档。
- **NASA 不背书本项目**；NASA 名称/徽标不用于暗示背书。所有第三方材料均已引用；
  若发现遗漏，请在提交前补充 `THIRD_PARTY_NOTICES.md`。
