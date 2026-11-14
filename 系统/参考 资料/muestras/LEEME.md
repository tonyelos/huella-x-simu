# 样本文件说明

两份都是**真实取回的 SPHEREx 光谱像切片**，不是占位文件，可以离线跑解析代码。

| 文件 | 版本 | 天区 | 大小 | 用途 |
|---|---|---|---|---|
| `M101_D2_2026W25_qr3_cutout.fits` | QR3 | M101（210.80227, +54.34895） | 3.95 MB | 演示"从任意坐标取切片" |
| `campoA_D6_2025W48_dr1_cutout.fits` | DR1 | 场 A（160.617, +30.67） | 3.95 MB | 本项目场 A 的真实一期数据，可直接喂给 m1/m2 |

- `cabeceras.txt`：两份文件的完整头信息（EPSF / WCS-WAVE 只保留前 40 张卡）
- `manifest.json`：文件大小、sha256、来源 URL、HDU 结构

## 打开方式

```python
from astropy.io import fits
hdul = fits.open("campoA_D6_2025W48_dr1_cutout.fits")
print(hdul.info())
# 0 PRIMARY / 1 IMAGE / 2 FLAGS / 3 VARIANCE / 4 ZODI / 5 EPSF / 6 WCS-WAVE
```

## 三个实测得到的结论

1. 两个版本的 `IMAGE` 都是 **12×12 像素**（0.02° 切片，像素尺度 6.2″）
2. `EPSF` 是 **8752×441**，不参与切片，占 3.86 MB —— 文件大就是因为这个扩展
3. `WCS-WAVE` 长度不同：QR3 是 1056，DR1 是 720。**跨版本合并数据时不要把这两张表当成同一张用**

## 复现命令

```bash
curl -o M101_D2_2026W25_qr3_cutout.fits \
  "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/2026W25_2A/l2b-v27-2026-273/2/level2_2026W25_2A_0279_1D2_spx_l2b-v27-2026-273.fits?center=210.80227,54.34895d&size=0.02"

curl -o campoA_D6_2025W48_dr1_cutout.fits \
  "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/2025W48_1A/l2b-v27-2026-208/6/level2_2025W48_1A_0658_4D6_spx_l2b-v27-2026-208.fits?center=160.617,30.67&size=0.02"
```
