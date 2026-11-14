# 赛道要求 ↔ HUELLA X 现状

对照基准：官方赛道原文（`01_挑战原文.md`）与仓库现有模块（`modulos/m1..m9`、`datos/m1_adquisicion/`）。
"状态"只根据仓库里能看到的代码与数据判断，没看到的标"待确认"。

| # | 官方要求 | 对应模块 | 状态 |
|---|---|---|---|
| 1 | 用户输入 RA/Dec | `m9_sitio/espectro3d.js`（`?campo=`）、`m1/campos_objetivo.json` | ⚠️ 部分：URL 只认预置的 `campo` 名，不支持任意 RA/Dec |
| 2 | 多期图像 → 时间序列 | `m1/seleccion_epocas.py`、`m2_alineacion` | ✅ 场 A 已有一对 22.7 h 的 A/B 期 |
| 3 | 闪烁 / 相加 / 相减查看 | `m3_deteccion`、`m4_movimiento`、`m9_sitio` | ✅ 已有差值图与轨迹 |
| 4 | **5 个指定天体必须能跑** | `m1/campos_objetivo.json` | ❌ 缺口：只声明了 `sphx_a`，且与 5 个目标相距 10°–97° |
| 5 | 用好 102 个波段 / 光谱 | `m6_huella_espectral` | ✅ 有 0.75–5 µm 光谱重建与模板匹配 |
| 6 | 6 个探测器阵列分别合图 | `m1` 的 `detector` 字段 | ✅ 数据里有探测器编号，合图逻辑可加 |
| 7 | 视场可调 | `m1/campos_objetivo.json` 的 `radio_grados` | ⚠️ 固定 0.2°，前端未暴露 |
| 8 | 叠加 Gaia / WISE | `m5_identificacion` | ⚠️ 待确认（交叉证认在 m5，是否取 Gaia/WISE 影像未确认） |
| 9 | 显示污染标记 | `m1/descarga_recortes.py`（要求 `FLAGS` 扩展）、`m6/fotometria.py`（`BIT_SOURCE=21`） | ✅ 已下载并参与掩膜 |
| 10 | URL 带参数便于嵌入 | `m9_sitio/espectro3d.js` | ⚠️ 仅 `?campo=`，无 `ra=&dec=` |
| 11 | 跟上数据发布节奏 | `m1/consulta_sia.py` 的 `COLECCIONES`（`spherex_qr3`、`spherex_dr1`、`spherex_dr1_deep`） | ✅ 集合可配置 |

## 三个最该先补的洞

### 1. 五个测试天体没有数据（直接影响验收）

`campos_objetivo.json` 目前只有场 A（RA 160.617, +30.67）。
官方验收用的 5 个目标最近的一个也差 10°（RW Aur ↔ 场 C），现有数据一条都用不上。

建议：把 `04_测试目标天体.csv` 里的坐标直接加成新字段，
例如 `barnard`(269.452077, +4.693365)、`epsind_b`(331.044147, −56.782844)、
`wise0855`(133.795129, −7.245147，**用赛道给的坐标**)、`rw_aur`(76.956526, +30.401438)、
`r_dra`(248.167611, +66.754979)，半径先给 0.2°（Eps Ind B 那组需要 0.3° 以上）。

注意 `seleccion_epocas.py` 有"同一探测器、间隔 ≤ 72 h"的配对约束 —— 这 5 个目标能否配出 A/B 对，
取决于各自天区在 qr3/dr1 里的实际扫描间隔，**需要先跑一遍 TAP 才知道**（IRSA 恢复后立刻验证）。

### 2. DataLink 是单点故障

`m1` 现在靠 DataLink 把观测 ID 换成文件路径。采集当天 DataLink 全线 502，这条路直接断。
已验证的降级路径（见 `03_SPHEREx数据访问笔记.md` 第 3.6 节）：从 S3 列处理版本目录 + 拼文件名，
不经过 DataLink 也能拿到切片，且实测 200。

### 3. 任意坐标查询（第 1 条）

官方原文是 "Users will enter the coordinates of a patch of sky"。
现在的 `?campo=sphx_a` 只覆盖预置天区。最小改动：`?ra=&dec=&fov=` 三个参数，
后端按坐标现取一次切片并缓存（切片本身只要 4 MB，EPSF 不取可以更小）。

## 一个可以直接引用的差异点

赛道把 RW Aur（金牛 T 型变星）和 R Dra（米拉变星）放进验收清单，是故意的：
这两个目标**位置不动、亮度在变**。用"两图相减不为零"的朴素判据会把它们误报成移动天体。

HUELLA X 的 `m4_movimiento` + `m6_huella_espectral` 组合正好能给出
"亮度变化，非位置变化"的结论 —— 这是相对"纯闪烁查看器"的实质优势，建议在演示里明确点出来。
