"""纯 Python 的 HEALPix NESTED 索引（只做 ang2pix，不需要 healpy/astropy）。

实现直接按 HEALPix 的标准 ang2pix_nest 算法：在最高分辨率 nside=8192 上求
面编号与 (x, y)，再把像素号降到目标 nside。这样边界舍入与官方实现一致；
`tests/test_star_catalog.py` 会用已知坐标与 C 参考值做回归。

为什么不用 healpy：
- 运行环境没有 healpy/astropy，而星表缓存只需要一个稳定的空间桶；
- 不引入编译依赖，离线可重建；
- 锥搜索仍以 (dec, ra) 矩形粗筛 + 大圆距离精筛为主，HEALPix 列是给
  未来按像素分区/懒加载用的，不参与正确性关键路径。
"""
from __future__ import annotations

import math

_NS_MAX = 8192
_NSIDE2 = {1 << k: (1 << k) ** 2 for k in range(0, 14)}


def _xy2pix() -> tuple[list[int], list[int]]:
    """HEALPix 的位交错表：把 7 bit 坐标展开到偶数/奇数位。"""
    x2pix = [0] * 128
    y2pix = [0] * 128
    for i in range(128):
        x = 0
        y = 0
        for j in range(7):
            bit = (i >> j) & 1
            x |= bit << (2 * j)
            y |= bit << (2 * j + 1)
        x2pix[i] = x
        y2pix[i] = y
    return x2pix, y2pix


_X2PIX, _Y2PIX = _xy2pix()


def ang2pix_nest(nside: int, ra_deg: float, dec_deg: float) -> int:
    """(ra, dec) [deg, ICRS] → HEALPix NESTED 像素号，范围 [0, 12*nside²)。

    nside 必须是 2 的幂（<=8192）。坐标必须有限；非法输入抛 ValueError。
    """
    if nside not in _NSIDE2:
        raise ValueError("nside 必须是 1..8192 的 2 的幂")
    ra = float(ra_deg)
    dec = float(dec_deg)
    if not (math.isfinite(ra) and math.isfinite(dec)):
        raise ValueError("ra/dec 必须是有限数字")
    if not (-90.0 <= dec <= 90.0):
        raise ValueError("dec 必须在 [-90, 90]")

    theta = math.radians(90.0 - dec)  # 余纬
    phi = math.radians(ra % 360.0)
    z = math.cos(theta)
    za = abs(z)
    twopi = 2.0 * math.pi
    piover2 = 0.5 * math.pi

    tt = (phi % twopi) / piover2  # [0, 4)
    if za <= 2.0 / 3.0:
        # 赤道区：两条斜边线的 y 截距决定面与 (x, y)
        jp = int(math.floor(_NS_MAX * (0.5 + tt - z * 0.75)))
        jm = int(math.floor(_NS_MAX * (0.5 + tt + z * 0.75)))
        ifp = jp // _NS_MAX
        ifm = jm // _NS_MAX
        if ifp == ifm:
            face_num = (ifp % 4) + 4
        elif ifp < ifm:
            face_num = ifp % 4
        else:
            face_num = (ifm % 4) + 8
        ix = jm % _NS_MAX
        iy = _NS_MAX - (jp % _NS_MAX) - 1
    else:
        # 极区：按离最近极点的距离在四个三角面内定位
        ntt = int(math.floor(tt))
        if ntt >= 4:
            ntt = 3
        tp = tt - ntt
        tmp = math.sqrt(3.0 * (1.0 - za))
        jp = int(math.floor(_NS_MAX * tp * tmp))
        jm = int(math.floor(_NS_MAX * (1.0 - tp) * tmp))
        jp = min(jp, _NS_MAX - 1)
        jm = min(jm, _NS_MAX - 1)
        if z >= 0:
            face_num = ntt
            ix = _NS_MAX - jm - 1
            iy = _NS_MAX - jp - 1
        else:
            face_num = ntt + 8
            ix = jp
            iy = jm

    ix_low = ix % 128
    ix_hi = ix // 128
    iy_low = iy % 128
    iy_hi = iy // 128
    ipf = (_X2PIX[ix_hi] + _Y2PIX[iy_hi]) * (128 * 128) + (_X2PIX[ix_low] + _Y2PIX[iy_low])
    scale = _NS_MAX // nside
    ipf //= scale * scale
    return ipf + face_num * nside * nside
