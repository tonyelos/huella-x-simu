# AstroNova / HUELLA X 深度分析

> 仓库：<https://github.com/Beltranpcb/AstroNova>
> 分析 SHA：`1253925ab13419b5d44db323548784c939a93a6e`
> 分析时间：2026-11-15
> 只读分析；未安装依赖、未运行其测试、未复制源码进产品仓库。

---

## 0. 一句话结论

**这是一个“公开静态演示 + 离线 Python 流水线 + 强契约/强叙事”的 NASA Space Apps 方案，适合借鉴数据契约、交互和科普表达；当前不是可复用的生产级识别系统，也没有真实数据验证。**

对我们的关系：

- 它的**移动天体/光谱/科普 UI**比我们完整；
- 我们的**真实数据、SQL/服务、Gaia 星表、多历元 BIC、官方 5 目标验证**比它完整；
- 它的代码**没有 LICENSE**，不能直接复制；
- 它适合当“产品表达/数据契约/交互参考”，不适合当“识别算法底座”。

---

## 1. 仓库事实

| 项 | 值 |
|---|---|
| 名称 | `Beltranpcb/AstroNova` |
| 产品名 | **HUELLA X** |
| 描述 | 无 |
| 语言 | Python |
| Stars / forks | **0 / 0** |
| 创建 | 2026-11-13 |
| 最近推送 | 2026-11-15 |
| archived | 否 |
| open issues | 1 |
| 默认分支 | `main` |
| 大小 | 3.0 MB |
| License | **无**（仓库无 `LICENSE` / `COPYING`） |
| 代码量 | 约 **12,190 行** Python/JS（含测试）；8 个模块 + 工具 + 静态站 |
| 最近提交 | `1253925` Merge PR #12 from develop |

状态判断：

- 非常新（两天），活跃；
- 0 star 不代表质量，只说明没有社区验证；
- **无 LICENSE 是复用硬阻断**；
- 只有 1 个 open issue，issue #11 是“真实数据测试”未完成。

---

## 2. 产品定位

README 的主张：

> 展示 SPHEREx 两次观测之间“什么在移动”，检查它是否已知，并从红外光谱推断“它由什么组成”。

三条主线：

```text
时间（A/B 闪烁/差异）
  → 运动（位置、速度、显著性）
  → 身份（JPL/MPC 已知天体）
  → 光谱（类型、温度、分子）
```

它明确说：

- 只说“候选”，不说“发现”；
- 没有匹配不能叫 Planet X；
- 两期观测不能证明新天体。

这一点与我们的边界一致，表达值得借鉴。

---

## 3. 架构形态

```text
m1 获取（IRSA TAP / S3 索引 / recortes）
  → m2 对齐（去黄道光、重投影、PNG、meta）
  → m3 检测（DAOStarFinder、掩膜、配对）
  → m4 运动（速度、显著性、分类）
  → m5 识别（JPL sb_ident / MPChecker）
  → m6 光谱（光度、Planck、模板、分子）
  → m7 发布（统一 JSON/PNG）
  → m9 静态 Web（无后端）
```

关键设计：

- 模块之间**不 import 彼此**，只通过 `datos/<modulo>/` 文件；
- 每个跨模块文件都有 JSON Schema 合同；
- 每个模块可独立测试；
- 静态 Web 只读 `m7_publicacion`，无后端；
- SkyBoT/Aladin/Chart 通过 CDN 在浏览器调用；
- 演示数据由 `m9_sitio/ejemplos/generar_datos.py` 生成，`origen: sintetico`。

这套架构在“可审计、可并行开发、demo 不会因后端挂掉”上很强，但代价是**没有实时数据库/服务**。

---

## 4. 实际实现程度（重要）

### 4.1 文档与代码状态不一致

`ARQUITECTURA.md:13` 仍写：

> “El repositorio contiene la arquitectura completa **sin código**：cada archivo existe con un comentario...”

但代码已经存在并且有实际实现（约 1.2 万行）。

`modulos/m1_adquisicion/LEEME.md:71` 写：

> “Esqueleto sin código: el comando de verificación falla...”

但 `seleccion_epocas.py`、`descarga_recortes.py` 实际已有较完整实现。

**判断：文档状态字段过期，不能按“代码不存在”或“模块已完成”二选一相信；必须看具体文件。**

### 4.2 演示数据 vs 真实数据

`datos/LEEME.md` 明确：

> “Nada de este directorio entra en git... 只有 `datos/` 下的合成演示由 `m9_sitio/ejemplos/generar_datos.py` 写出。”

`modulos/m9_sitio/ejemplos/generar_datos.py` 生成三个字段：

- `slipher`：合成小行星
- `atlas`：合成彗星
- `luhman`：合成褐矮星

README 也承认：

- “Datos de la web hoy: **Demostración**”
- “Los tres campos son sintéticos”

### 4.3 真实数据为什么没进来

`docs/analisis_de_objetos.md` 写：

- IRSA TAP 2026-11-14 返回 502；
- 真实 pipeline 没跑；
- 官方 5 个测试目标（Barnard/Eps Ind B/WISE 0855/RW Aur/R Dra）**没有 FITS**，状态为 pending；
- Gaia DR3 仍 pending（ESA 文件 CORS/网络问题）；
- 模板是合成的；
- 分子组成只做过合成验证。

### 4.4 所以它是什么

准确说：

> **一套设计完整、部分实现、以合成数据演示的静态 Web 原型 + 离线 pipeline 规格。**

不是：

> “已经在真实 SPHEREx 数据上跑通的生产系统”。

---

## 5. 核心代码审阅

### 5.1 m1 获取

文件：

- `modulos/m1_adquisicion/consulta_sia.py`
- `modulos/m1_adquisicion/indice_s3.py`
- `modulos/m1_adquisicion/seleccion_epocas.py`
- `modulos/m1_adquisicion/descarga_recortes.py`

优点：

- 明确知道 **SIA 不索引 SPHEREx**，改走 TAP `spherex.obscore`；
- 有 S3 Range 头部索引作为 TAP 失败 fallback；
- 处理 DataLink 两段式；
- 对 503 有重试、缩小 cutout 尺寸、重新选 trio；
- 明确 `EPSF`/`PSF` 命名差异；
- 选择 A/B 同 detector、0.5–72 h、模板 >120 d。

风险：

- 依赖 IRSA/网络；真实 TAP 当前不可用；
- `seleccion_epocas.py` 的“模板可跨 detector”会让 PSF/波长不完全一致，但用于固定星识别可接受；
- 只有 2 个真实目标场（`sphx_a`/`sphx_b`），不是任意天区服务。

### 5.2 m3 检测

文件：`modulos/m3_deteccion/deteccion_fuentes.py`

- `Background2D` + `DAOStarFinder`；
- 5σ；
- FWHM=2 px；
- aperture 1.5×FWHM；
- FLAGS 掩膜，bit 21 `SOURCE` 视为“信息位”，不屏蔽；
- 角距/通量单位转换。

风险：

- 6.15″/px 下 DAOStarFinder 的 FWHM=2、5σ 会产生混合/边缘源；
- 没有 EPSF/PSF 拟合、没有 deblend；
- 没有真实数据验证；
- 与官方 SExtractor/Tractor 路线不同，但对 demo 够用。

### 5.3 配对/运动

- `emparejamiento.py`：同一像素网格 1 px 内贪心匹配，标记 estacionaria/huerfana/descartada；
- `propuesta_parejas.py`：A/B 孤儿源，速度 0.05–150″/h，亮度比 ≤2，边缘 5 px，贪心最近邻；
- `clasificacion.py`：significance <2 estacionaria，2–5 no_concluyente，≥5 candidata。

风险：

- 只有 A/B 两期；
- 无 BIC/加速度/周期/多假设；
- 贪心配对在拥挤场会错配；
- 不能区分小行星/彗星/变星/伪源，只是候选。

### 5.4 m5 识别

文件：`modulos/m5_identificacion/consulta_catalogos.py`, `cruce_posicional.py`

- JPL `sb_ident` + MPChecker；
- 15″ 容差；
- 缓存到 `datos/m5_identificacion/cache/`；
- 只对非 estacionaria；
- 明确 geocentric 近似。

关键缺口：

- **没有 Gaia/2MASS/WISE/SIMBAD**；
- 不识别恒星/星系，只识别太阳系小天体；
- 15″ 对 6.15″/px 是 2.4 px，偏宽；
- 没有真实 cache 数据在仓库中。

### 5.5 m6 光谱

- 从多 detector cutout 取光谱点；
- Planck 拟合温度（50–3000 K，3+ 点）；
- 4 个**合成**模板（岩质/暗质/彗星/褐矮星）χ²；
- 5 种分子窗口，状态 `sin_rasgo/compatible/candidata/detectada/datos_insuficientes`；
- `detectada` 需 5 条同时成立，考虑 SPHEREx 分辨率；
- 警告“模板合成/光谱未真实验证”。

优点：

- 对“候选 vs 检测”的表达非常克制；
- 有自由度、误差、分辨率、替代解释；
- 分子窗口引用文献；
- UI 把“合成”标出来。

风险：

- 模板合成，不是实测库；
- 真实观测未验证；
- 2–4 个宽点做 Planck/分子，仍可能过拟合；
- 真实数据没有进入仓库。

### 5.6 m9 静态 Web

- 闪烁/帘幕/差分/轨迹；
- 点击 WCS → RA/Dec；
- SkyBoT 实时查询；
- 候选表、CSV、候选卡、Aladin；
- 光谱比较、模板比较、3D 光谱；
- 明确 `origen`（synthetic/real/pending）和“候选”文案；
- 无后端，CDN 依赖。

优点：

- 交互和信息设计明显强于普通 hackathon；
- 对“候选不是发现”的表达非常成熟；
- 可访问性/状态文字/降级错误做得细。

风险：

- 静态演示不等于可运行服务；
- 真实数据没有；
- SkyBoT/Aladin 外部依赖；
- 无后端就无法做星表缓存/批量识别/权限/审计。

---

## 6. 测试与工程质量

README/docs 声称：

- Python 162 tests；
- JS 55 tests；
- 每个模块独立验证；
- 端到端 synthetic test。

实际看：

- 约 **171 个 `def prueba_`**、**52 个 `test(`** 出现（静态计数，未执行）；
- 测试大量使用 synthetic fixtures；
- `m1` 的真实测试注释仍写“Esqueleto”；
- 未安装依赖/未运行，不能确认 pass；
- 无 CI 配置（未发现 `.github/workflows`）；
- 无 LICENSE；
- 无 release/tag；
- 0 star，没有外部使用验证。

工程优点：

- 合同先行、模块解耦；
- 对错误/空数据/降级有明确文案；
- 数据来源/合成标记非常认真；
- 文档量极大。

工程风险：

- 文档状态漂移（“无代码” vs 1.2 万行；“skeleton” vs 实现）；
- 测试可信度需要实际跑；
- 无许可证；
- 单人/短周期项目，维护不确定。

---

## 7. 与外部 `sphx_b` 数据包的关系

我们之前审查的 `huella_x_sphx_b_completo.zip`：

- 字段名 `sphx_b`；
- `meta.json` 写 `"Generado por m2_alineacion"`；
- 文件命名/JSON 合同与本仓库 m2–m7 高度一致；
- `sphx_b` 正是本仓库 `campos_objetivo.json` 中的目标场。

判断：

> **该 zip 很可能是 AstroNova 这条 pipeline 对 `sphx_b` 的产物，而不是官方原始数据包。** 这一点是“高度一致”的推断，作者身份未验证。

这解释了我们之前看到的现象：

- 原始 recorte 是真实 DR1；
- 派生 JSON 的候选/光谱/温度为该 pipeline 输出；
- 只有 2 个历元，候选不能确认；
- 头部/派生数据有问题；
- 没有代码/许可/缓存，无法复现。

---

## 8. 对我们的价值：三栏判定

### 必须自己做（不能借它替代）

| 能力 | 原因 |
|---|---|
| 真实 SQL/DB + 历元/覆盖 | 它是静态 JSON，无数据库 |
| 真实 Gaia/2MASS/WISE 星表缓存 | 它的 Gaia 仍 pending；只有 JPL/MPC |
| 官方 5 目标 + 多历元 BIC | 它没有官方目标 FITS，只有 A/B 两期 |
| 已知/未知恒星的 known/ambiguous/no_match | 它没有恒星目录身份 |
| 批量识别/候选目录/审计 | 它是静态站，不做服务端批量 |
| 可重复的真实数据验收 | 它主要是 synthetic demo |

### 可以借（低成本、高收益）

1. **数据合同/JSON Schema + 模块边界**：把我们的 `/api` 响应和 pipeline 中间产物再合同化；
2. **候选 provenance**：每个对象标 `real/synthetic/pending`，并在 UI 明示；
3. **闪烁/帘幕/差分三种视图**：我们可加到 View/MoveScope；
4. **点击 WCS → RA/Dec**：我们的 View 已有类似，但可借其交互细节；
5. **SkyBoT 实时查询**：补我们缺失的太阳系已知天体核验；
6. **光谱比较/模板 χ²/分子状态**：作为“候选解释”UI，不要当科学结论；
7. **3D 光谱/候选目录/比较器**：科普展示强；
8. **状态文字、降级错误、无障碍**：直接借鉴设计原则。

### 坚决不借（会把产品变成另一种产品）

| 机制 | 为什么不能借 |
|---|---|
| 静态 Web + 预生成 JSON 作为主架构 | 我们已经选择真实服务/DB；退回去会失去实时性和数据一致性 |
| 以合成模板/合成 demo 充当科学证据 | 我们要求真实数据和诚实 provenance |
| 只用 A/B 两期 + 速度阈值做运动结论 | 我们已有 BIC/多历元/候选分级 |
| 用 JPL/MPC 代替恒星身份 | 恒星/褐矮星需要 Gaia/2MASS/WISE/SIMBAD |
| 直接复制其代码 | **无 LICENSE**，不可复用 |
| 把“detectada/candidata”分子状态当确认 | 真实观测验证仍 pending |

---

## 9. 关键文件索引（抽查用）

| 路径 | 用途 |
|---|---|
| `README.md` | 产品叙事、功能、状态、限制 |
| `ARQUITECTURA.md` | 8 模块架构、合同、拓扑排序 |
| `modulos/m1_adquisicion/consulta_sia.py` | IRSA TAP 查询 |
| `modulos/m1_adquisicion/indice_s3.py` | S3 头部索引 fallback |
| `modulos/m1_adquisicion/seleccion_epocas.py` | A/B/模板选择 |
| `modulos/m1_adquisicion/descarga_recortes.py` | DataLink/cutout 下载 |
| `modulos/m2_alineacion/reproyeccion.py` | 共同网格重投影 |
| `modulos/m3_deteccion/deteccion_fuentes.py` | DAOStarFinder 检测 |
| `modulos/m3_deteccion/propuesta_parejas.py` | A/B 配对与速度限制 |
| `modulos/m4_movimiento/clasificacion.py` | 2/5σ 分类 |
| `modulos/m5_identificacion/consulta_catalogos.py` | JPL/MPC 查询 |
| `modulos/m5_identificacion/cruce_posicional.py` | 15″ 位置匹配 |
| `modulos/m6_huella_espectral/ajuste_planck.py` | Planck 温度 |
| `modulos/m6_huella_espectral/comparacion_plantillas.py` | 合成模板 χ² |
| `modulos/m6_huella_espectral/identificacion_molecular.py` | 分子状态机 |
| `modulos/m7_publicacion/union_candidatos.py` | 发布候选 |
| `modulos/m9_sitio/index.html` | 静态 Web |
| `modulos/m9_sitio/ejemplos/generar_datos.py` | 三个合成 demo 字段 |
| `docs/analisis_de_objetos.md` | 状态：Gaia/pending、真实数据未跑 |
| `datos/LEEME.md` | 数据不进 git / 合成 demo |

---

## 10. 抽查记录

我实际核了 4 条：

1. **“仓库无代码/只是架构”**：`ARQUITECTURA.md:13` 这么说，但代码约 1.2 万行、模块有实现；**文档过期**。
2. **“真实数据”**：`datos/LEEME.md` + `m9_sitio/ejemplos/generar_datos.py` 显示 demo 是 `slipher/atlas/luhman` 合成场；**不是真实数据**。
3. **“Gaia 识别”**：`docs/analisis_de_objetos.md:47`、`README.md:603` 明确 Gaia pending；`m5` 只查 JPL/MPC；**没有恒星目录**。
4. **“许可证”**：仓库无 LICENSE；**不能复制代码**。

---

## 11. 最终判断

| 维度 | 评价 |
|---|---|
| 产品叙事/UI | **强**，值得借鉴 |
| 数据合同/模块化 | **强**，值得借鉴 |
| 真实数据 | **弱/未验证**，demo 合成，真实 pipeline 没跑 |
| 恒星身份识别 | **缺失**，Gaia pending |
| 运动算法 | 只 A/B 两期，弱于我们 |
| 光谱/分子 | 设计克制，但模板合成、真实验证 pending |
| 服务化/数据库 | **无后端**，不适合替代我们的架构 |
| 可复用代码 | **不可**（无 LICENSE） |
| 作为竞品/参考 | **高价值**，尤其 UI/合同/科普表达 |

**一句话：AstroNova 是“优秀的方案与演示设计”，不是“已验证的真实识别系统”。我们可以借它的表达、合同和交互，但不能借它的科学结论，也不能复制它的代码。**
