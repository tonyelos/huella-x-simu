# 外部数据包审查：huella_x_sphx_b_completo.zip

> 审查对象：`/Users/yiao/Downloads/huella_x_sphx_b_completo.zip`
> SHA-256：`bbfaae91c92f894ac026d8537e54c2175de404425524f6c93170cd7c190cf86f`
> 审查时间：2026-11-15
> 结论：**原始 SPHEREx DR1 cutout 可用；随包派生的候选/光谱/温度/成分结论不可直接作为科学结论使用。**

---

## 1. 包内容

- 98 MB，54 个条目（含重复路径），zip 完整性检查通过；
- 使用反斜杠路径，macOS `unzip` 会提示重复/覆盖；Python `zipfile` 可正常读；
- 字段：
  - 名称：Campo B en la eclíptica
  - 中心：RA **158.578°**，Dec **9.595°**
  - 视场：0.2° 半径；cutout **234×234 px**，6.1528″/px
  - 探测器：D3 主；另有 D2/D5/D6 光谱序列
- 23 个 `recortes/*.fits`，与 `seleccion.json` 的 23 条一一对应；
- `listado_exposiciones.json` 116 条候选曝光；
- 3 个 D3 历元：
  - A：`2025W48_2A_0656_1_D3`，MJD 61009.32903213
  - B：`2025W48_2A_0706_4_D3`，MJD 61009.537574757（与 A 相隔 5.005 h）
  - 模板：`2025W21_1B_0003_4_D3`，MJD 60814.015565243（早约 195 天）
- 派生文件：`epoca_a/b`、`plantilla`、`diferencia.png`、`sin_zodiacal/`、`detecciones/fuentes/situaciones/candidatos`、`movimiento/cinematica/significancia/identificacion`、`espectros/temperatura/tipo_espectral/composicion`。

---

## 2. 原始数据：可用

### 2.1 FITS 结构

`recortes/*.fits` 是官方 level2 MEF：

| HDU | 内容 |
|---|---|
| PRIMARY | 无数据 |
| IMAGE | 234×234，float32，MJy |
| FLAGS | 234×234，int32 |
| VARIANCE | 234×234，float32，MJy² |
| ZODI | 234×234，float32，MJy |
| EPSF | BINTABLE，约 451 行 |
| WCS-WAVE | BINTABLE，1 行 |

- 与官方 IRSA cutout 对照：下载同帧官方 0.2° cutout，`BUNIT=MJy`、`CRVAL/CRPIX` 同源；单位不是包的错误。
- WCS 解析：
  - `CRVAL=157.607782931, 8.62388307503`，`CRPIX=424.5,-630.5`，PC+CDELT+SIP；
  - 用项目 `core.wcs` 反算 234×234 中心：**RA 158.5784°, Dec 9.5944°**，与 `meta.json` 的 158.578/9.595 一致（<0.001°）。
- 图像统计：
  - `epoca_a`：finite 99.98%，median 0.0235 MJy，max 2364 MJy；
  - `epoca_b`：finite 98.5%，median 0.0201 MJy，max 402 MJy；
  - `plantilla`：finite 98.3%，median 0.0230 MJy；
  - FLAGS 非零约 19k–21k / 54,756 像素，使用前必须按位掩膜。
- **C 管线兼容性实测**：用 `系统/anyfield/pixel_ingest.detect_file_sources()` 直接读 `2025W48_2A_0656_1_D3.fits`，成功检测 **65 个源**；说明原始 recorte 可被 C-P1A 读入（无需改造）。

### 2.2 可用的用途

- 作为**第 6 个独立天区**（黄道 Campo B）做差异成像/移动候选；
- 用 A/B 5 小时 + 模板 195 天做时域/伪源测试；
- 用 D2/D3/D5/D6 的 20 个谱段 cutout 做多波段 SED/光谱测试；
- 用 A/B/模板做层叠/差异/PSF 测试。

---

## 3. 派生数据：不能直接当结论

### 3.1 候选

`candidatos.json`：6 个 A-B 配对：

| ID | 速度 (″/h) | 显著性 | 状态 |
|---|---:|---:|---|
| p001 | 10.25 | 44.68 | sin_identificar |
| p002 | 17.11 | 82.53 | sin_identificar |
| p003 | 23.28 | 80.41 | sin_identificar |
| p004 | 26.56 | 96.95 | sin_identificar |
| p005 | 29.93 | 116.31 | sin_identificar |
| p006 | 60.77 | 430.14 | sin_identificar |

问题：

- **只有 A/B 两个历元**，间隔 5 h；没有第三条轨道点，不能定轨道/确认；
- 60″/h 级别在黄道面很可能是太阳系天体/伪源；没有 MPC/JPL 匹配；
- `identificacion.json` 6 条全部 `sin_identificar`，未给出已知天体名称；
- 没有候选的第三方星表/历表核验；
- 因此只能叫 `moving_candidate`，不能叫“新天体/新行星”。

### 3.2 光谱/温度/类型/成分

- `espectros.json`：每个候选仅 **2–4 个宽波段点**（1.2–4.6 µm），部分 flux 为负；
- `espectro_medido.json` 没有有效 S/N（`senal_ruido` 为 None 或负）；
- `tipo_espectral.json`：6 个全部 **indeterminado**，最佳模板 χ²_reduced 15–107，拟合不可信；
- `temperatura.json`：5 个给出 ~1100–1320 K，但基于 2–4 个宽点 + 简单 Planck 连续谱，属于**模型拟合值，不是测量**；
- `composicion.json`：所有分子状态都是 `datos_insuficientes`，没有一条“detected”；
- 结论：这些派生字段只能作为**待验证假设/演示**，不能写进科学结论。

### 3.3 派生 FITS 的头问题

- `epoca_b.fits`：
  - PRIMARY `MJD-OBS=61009.537574757`（正确）
  - IMAGE `MJD-OBS=61009.32903213`（错，复制了 A 的时间）
- `plantilla.fits`：
  - PRIMARY `MJD-OBS=60814.015565243`（正确）
  - IMAGE `MJD-OBS=61009.32903213`（错）
- `epoca_a/b/plantilla` 是重采样/派生版本，没有 EPSF/WCS-WAVE；不要用它们做时间/PSF 分析。
- 结论：**时间一律以 `seleccion.json` / PRIMARY 头为准**；不要信派生 IMAGE 头的 MJD。

---

## 4. 可复现性/合规性

- zip 内**没有处理代码、README、许可证、校验清单**；
- `identificacion.json` 引用 `archivo_cache: sphx_b_p001.json`，但包内没有这些缓存文件，无法复现匹配；
- 派生 JSON 没有写清算法版本/参数（只有部分 `version_contrato`/注释）；
- FITS 头没有 `TELESCOP/INSTRUME/ORIGIN` 等完整任务元数据，但有 IRSA 路径/DR1 版本；
- 若使用，必须补：
  - IRSA/SPHEREx 官方引用与 DOI；
  - 数据来源 URL（`seleccion.json` 已有）；
  - 派生结果标注“第三方生成/未复现”；
  - 不把 `sin_identificar` 写成发现。

---

## 5. 建议

### 可以立即用

1. `recortes/*.fits` 做 C-P1A/P1B 的**第 6 个测试天区**；
2. A/B 5 h + 模板 195 d 做差异/伪源/运动候选测试；
3. D2/D3/D5/D6 20 个 cutout 做多波段 SED 原型。

### 不要直接用

1. `candidatos.json` 的 6 个“候选”不能当确认；
2. `temperatura/tipo_espectral/composicion` 不能当物理结论；
3. 派生 `epoca_*` 的 IMAGE MJD 不可信；
4. 不要用 `identificacion.json` 的 `sin_identificar` 推断“新天体”。

### 建议的下一步验证

1. 用 MPC/JPL Horizons/SkyBot 对 6 个候选做已知天体匹配；
2. 找第三历元（同一夜或相邻夜）拟合轨道；
3. 用官方 `recortes` 重跑差异成像/PSF 检查；
4. 把 Campo B 作为 C 的独立测试场，写进验收；
5. 如果入库，保留原始 URL、SHA-256、派生版本、掩膜版本。

---

## 6. 最终判定

| 问题 | 结论 |
|---|---|
| 原始 SPHEREx 数据可用吗？ | **可用**；真实 DR1 MEF，WCS/FLAGS/VARIANCE/EPSF 完整，C 管线可读 |
| 能作为第 6 个天区吗？ | **可以**；黄道 Campo B，适合差异成像/多波段测试 |
| 能直接信 6 个候选吗？ | **不能**；只有 2 历元，全部未识别，无轨道/无第三方匹配 |
| 能直接信光谱/温度/类型/成分吗？ | **不能**；2–4 个宽点、负 flux、χ² 差、成分全部 insufficient |
| 能直接入生产库吗？ | **不建议直接入**；先修 MJD 头、补 provenance/许可、独立验证候选 |
