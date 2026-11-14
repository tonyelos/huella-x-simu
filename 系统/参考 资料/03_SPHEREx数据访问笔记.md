# SPHEREx 数据访问笔记（实测）

这份笔记只写**验证过的事实**，每条都注明是通过什么方式确认的。
采集时间：2026-11-14 21:56–22:15（Europe/Madrid）。

---

## 1. 数据发布版本（决定你该抓哪一版）

| 版本 | 状态 | 说明 |
|---|---|---|
| QR1 | 已退役（2026-02 起） | IRSA 不再提供 |
| QR2 | 不再出现在检索结果里，**直链可下到 2027-02** | 被 DR1 取代 |
| QR3 | 活跃，2026-09 起 | PSF 与光度定标更新 |
| DR1 | 活跃，2026-11 发布 | 把 QR2 数据重处理，与 QR3 合成统一处理集 |

> 结论：**新项目只碰 QR3 和 DR1**，两版一起用才能得到时间上连续、处理一致的时序。

来源：<https://irsa.ipac.caltech.edu/data/SPHEREx/docs/overview_qr.html>

## 2. 一个 level2 光谱像文件长什么样

- 单个文件约 **65 MB**（实测 S3 列举：64 938 240 – 65 931 840 字节）
- 空间 2040×2040 像素，像素尺度 **≈ 6.2 角秒**（由切片实测反推：0.02° 切片 = 72″ → 12 px）
- 7 个 HDU：`PRIMARY` / `IMAGE` / `FLAGS` / `VARIANCE` / `ZODI` / `EPSF` / `WCS-WAVE`
  - `FLAGS` 就是赛道里说的 **contamination flags**（污染标记），加分项里点名要显示
  - `WCS-WAVE` 是波长解算表（102 波段的波长映射）
  - 因为滤光片是线性渐变（linear variable filter），**同一张图上每一行对应不同波长**
- 6 个探测器 D1–D6，各覆盖 0.75–5.0 µm 中的一段；波段名形如 `SPHEREx-D2`

文件名规律：

```
level2_<周>_<扫描>_<场序号>_<探测器>_spx_<处理版本>.fits
例：level2_2026W25_2A_0279_1D2_spx_l2b-v27-2026-273.fits
                                 ↑ ↑
                              场序号 探测器2(D2)
```

## 3. 三条访问路径 + 实测结果

### 3.1 静态切片（✅ 可用，推荐用于取图）

只要你知道文件的完整路径，就可以用 `center` / `size` 参数让它只返回该天区的一小块：

```
https://irsa.ipac.caltech.edu/ibe/data/spherex/<release>/level2/<周>/<处理版本>/<探测器>/<文件>.fits?center=<RA>,<Dec>d&size=<度>
```

实测（2026-11-14 22:05）：

```bash
curl -o cut.fits "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/2026W25_2A/l2b-v27-2026-273/2/level2_2026W25_2A_0279_1D2_spx_l2b-v27-2026-273.fits?center=210.80227,54.34895d&size=0.02"
# → 200, 3 954 240 字节, application/fits
```

**两个必须知道的坑**：

1. `size` 单位是**度**，不是角分，也不是像素。
2. 切片**只作用于 IMAGE / FLAGS / VARIANCE / ZODI**。`EPSF`（8752×441）和 `WCS-WAVE` 保持原尺寸，
   所以 3.95 MB 里有 3.86 MB 是 EPSF —— **切得再小，文件也下不来 4 MB 以下**。
   要做"移动端友好"，得自己从 MEF 里只取 IMAGE + FLAGS（约 1 KB/波段），或者让服务端做二次裁剪。

### 3.2 SIA v2 检索（⚠️ 对 SPHEREx 支持存疑 + 当天 502）

```
https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_qr3&POS=CIRCLE <RA> <Dec> <半径>&MAXREC=10
```

实测：

- `COLLECTION=spherex_qr3` / `spherex_dr1`：`502 Proxy Error` 或 30–280 s 超时
- `COLLECTION=spherex_qr2`：HTTP 200，`QUERY_STATUS=OK`，但 **0 行**
- POST/GET、0.05°/0.1°/1° 半径都试过，结果一致

两种解释无法在此环境区分：
(a) IRSA 当天上游故障；(b) SIA 的 SPHEREx 集合根本没有建索引。

**旁证偏向 (b)**：本项目 `modulos/m1_adquisicion/consulta_sia.py` 的文件头已记录
"IRSA 的 SIA 只覆盖 wise / twomass / ptf，不索引 SPHEREx（见 issue #2）"，
并因此改用 TAP。

**结论：不要指望 SIA 找 SPHEREx 文件，用 TAP。**

### 3.3 TAP / ADQL（❌ 采集当天全挂，但表结构已确认）

```bash
curl -G "https://irsa.ipac.caltech.edu/TAP/sync" \
  --data-urlencode "REQUEST=doQuery" --data-urlencode "LANG=ADQL" \
  --data-urlencode "FORMAT=csv" \
  --data-urlencode "QUERY=SELECT TOP 5 obs_id,s_ra,s_dec,t_min,access_url FROM spherex.obscore WHERE q3c_radial_query(s_ra,s_dec,210.8,54.35,0.1)=1"
```

确认可用的表（21:58 时 `TAP_SCHEMA` 查询成功，内容如下）：

```
spherex.obscore          ← 观测元数据总表（IVOA ObsCore），按天区检索用这张
spherex.observation      ← 观测（一次扫描）
spherex.plane            ← 数据平面（一个波段/一次曝光），含 poly 天区多边形
spherex.artifact         ← 具体文件，含 uri
spherex.provenance_input ← 处理溯源
```

`obscore` 关键列：`obs_id, s_ra, s_dec, t_min, t_max, em_min, em_max, access_url, access_estsize, calib_level`。

本项目 m1 已验证可用的集合名：`spherex_qr3`、`spherex_dr1`、`spherex_dr1_deep`（见 `consulta_sia.py` 的 `COLECCIONES`）。

**22:00 之后**，sync 与 async（`/TAP/async`）都变成 502，说明是服务端不可用，不是我们写法错。

### 3.4 DataLink（❌ 采集当天 502；本项目 m1 现在用的就是这个）

```
https://irsa.ipac.caltech.edu/datalink/links/spherex?ID=ivo://irsa.ipac/spherex_dr1?<观测ID>/D<探测器>
```

给一个观测 ID，返回一组候选文件（不同处理版本、不同产品的下载链接）。
`datos/m1_adquisicion/*/listado_exposiciones.json` 里存的 `access_url` 就是这种形式。

**风险提示**：这条链路一旦挂，m1 就抓不到数。已实测的降级路径见下面 **3.6**，
不经过 DataLink 也能拿到切片。

### 3.5 AWS S3 公开桶（✅ 可用，适合批量与镜像）

```
桶：nasa-irsa-spherex   区域：us-east-1
前缀：dr1/   qr3/   qr2/
匿名可列：https://nasa-irsa-spherex.s3.amazonaws.com/?list-type=2&prefix=qr3/level2/&delimiter=/
```

实测结构：

```
qr3/level2/2026W24_1A/l2b-v27-2026-271/1/level2_2026W24_1A_0001_1D1_spx_l2b-v27-2026-271.fits   (65 MB)
dr1/hips5_cube/hips_20261026/Norder5/Dir0/Npix0_cube.fits                                        (321 MB)
```

- 无凭证、无流量费（AWS Open Data）
- `dr1/hips5_cube/` 是全天真彩 HiPS 立方，单块 320 MB，**不要整块拉**
- 桶里没有"按坐标索引"的文件，所以 S3 解决的是"已知路径的批量下载"，不解决"从坐标找文件"


### 3.6 绕过 DataLink 的降级路径（✅ 实测可用，建议加进 m1）

DataLink 挂掉时，m1 现在会卡在"由观测 ID 换文件路径"这一步。实测发现这条路能自己走通：

```
观测 ID：2025W48_1A_0658_4/D6
   ↓ 拆成：周 2025W48_1A / 场 0658 / 重复 4 / 探测器 D6
   ↓ 处理版本从 S3 列出来（dr1/level2/2025W48_1A/ 下只有一个：l2b-v27-2026-208）
相对路径：dr1/level2/2025W48_1A/l2b-v27-2026-208/6/level2_2025W48_1A_0658_4D6_spx_l2b-v27-2026-208.fits
   ↓ 加切片参数
https://irsa.ipac.caltech.edu/ibe/data/spherex/<相对路径>?center=160.617,30.67&size=0.02
   → 200，3 951 360 字节
```

下面这段是**跑通过的完整代码**（纯标准库，不需要 astropy）：

```python
import re, urllib.request, urllib.error

S3   = "https://nasa-irsa-spherex.s3.amazonaws.com/?list-type=2&prefix={p}&delimiter=/&max-keys=50"
BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"

def _get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()

def version_de_proceso(release, semana):
    """处理版本目录（如 l2b-v27-2026-208），从 S3 前缀现查，不硬编码。"""
    xml = _get(S3.format(p=f"{release}/level2/{semana}/")).decode()
    return [x for x in re.findall(r"<Prefix>([^<]+)</Prefix>", xml) if x.count("/") == 4][0].split("/")[-2]

def ruta_fits(release, obs_id, detector):
    """obs_id = '2025W48_1A_0658_4'（周_场_重复），detector = 6"""
    semana, campo, rep = obs_id.rsplit("_", 2)
    proc = version_de_proceso(release, semana)
    return (f"{release}/level2/{semana}/{proc}/{detector}/"
            f"level2_{semana}_{campo}_{rep}D{detector}_spx_{proc}.fits")

def url_recorte(release, obs_id, detector, ra, dec, size):
    return f"{BASE}{ruta_fits(release, obs_id, detector)}?center={ra},{dec}&size={size}"

# 自测：2025W48_1A_0658_4 / D6  →  HTTP 200, 3 951 360 字节, 首字节 b'SIMPLE'
```

> 注意：观测 ID 的 `2025W48_1A` 本身含下划线，所以用 `rsplit("_", 2)` 从右往左切两刀，
> 恰好得到"周名 / 场号 / 重复号"，写成 `split("_")` 会切错。文件名的完整格式是：
> `level2_<周>_<场号>_<重复号>D<探测器>_spx_<处理版本>.fits`

**这条路径的三个好处**：
1. 不依赖 DataLink 和 TAP，只要静态文件服务在就能取数
2. 可以直连 S3（`https://nasa-irsa-spherex.s3.amazonaws.com/<相对路径>`）做 Range 读取
3. 处理版本可以从 S3 前缀现查，不用硬编码

**一个必须注意的返回值**：

```
422  Arrays do not overlap.
```

意思是"这个文件的天区不覆盖你给的坐标"。切片服务会做覆盖校验，所以它也能当**免费的覆盖判断**用：
请求前不用先去 TAP 查，直接试一次就知道该文件是否覆盖目标位置（返回值只有 22 字节，很便宜）。

## 4. 给 HUELLA X 的三条落地建议

1. **两级缓存**：IRSA 的检索（SIA/TAP）只用来"按坐标找文件列表"，结果落成本地 JSON（就像现在的 `listado_exposiciones.json`）；
   真正的图像用 `?center&size` 切片取，按坐标哈希缓存在服务端，网页只读缓存。这样 IRSA 抖动不影响演示。
2. **只取需要的 HDU**：`EPSF` 占了切片文件的 97%，网页端根本不用它。服务端切片时只保留 IMAGE/FLAGS，能把单次传输从 4 MB 压到几十 KB。
3. **时间轴数据源**：同一位置在 QR3 和 DR1 里各有一份，合并时按 `t_min` 排序、按处理版本去重（DR1 与 QR3 的处理版本不同，不能混着做光度测量）。

## 5. 复现实测的命令

```bash
# 1) 表结构（当时可用，22:00 后同端点 502）
curl -G "https://irsa.ipac.caltech.edu/TAP/sync" \
  --data-urlencode "REQUEST=doQuery" --data-urlencode "LANG=ADQL" --data-urlencode "FORMAT=csv" \
  --data-urlencode "QUERY=SELECT table_name FROM TAP_SCHEMA.tables WHERE table_name LIKE '%spherex%'"

# 2) 静态切片（✅）
curl -o muestras/M101_D2_2026W25_cutout.fits \
  "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/2026W25_2A/l2b-v27-2026-273/2/level2_2026W25_2A_0279_1D2_spx_l2b-v27-2026-273.fits?center=210.80227,54.34895d&size=0.02"

# 3) S3 匿名列举（✅）
curl "https://nasa-irsa-spherex.s3.amazonaws.com/?list-type=2&prefix=qr3/level2/&delimiter=/&max-keys=20"
```
