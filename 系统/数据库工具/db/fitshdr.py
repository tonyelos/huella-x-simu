"""最小 FITS 头解析器（只读头，不碰像素）。

为什么不用 astropy
------------------
运行环境没有 astropy，而数据库只需要 45 KB 头里的十几个键。这里按 FITS 标准
80 字节卡片扫描，支持多块头、跳过数据区、解析字符串/布尔/数字，足够处理
SPHEREx level2 的 PRIMARY + IMAGE + FLAGS + ... 结构。

一个容易踩的坑：SPHEREx 的头部字符串值里会出现 `/`（例如 `'MJy / sr'`），
所以注释切分必须在引号闭合之后做。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


class HeaderError(ValueError):
    """头文件缺关键卡片，不能入库。"""


_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?$")


def _comment_index(field: str) -> int:
    """返回引号外的第一个 '/' 的下标；没有则 -1。"""
    in_quote = False
    i = 0
    while i < len(field):
        ch = field[i]
        if ch == "'":
            if in_quote and i + 1 < len(field) and field[i + 1] == "'":
                i += 2
                continue
            in_quote = not in_quote
        elif ch == "/" and not in_quote:
            return i
        i += 1
    return -1


def parse_value(field: str) -> Any:
    """把 FITS 卡片的 value 部分转成 Python 标量。"""
    field = field.strip()
    if not field:
        return None
    cut = _comment_index(field)
    if cut >= 0:
        field = field[:cut].rstrip()
    if not field:
        return None

    if field[0] == "'":
        out: list[str] = []
        i = 1
        while i < len(field):
            ch = field[i]
            if ch == "'":
                if i + 1 < len(field) and field[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(ch)
            i += 1
        return "".join(out).rstrip()

    token = field.strip()
    if token == "T":
        return True
    if token == "F":
        return False
    if _NUM_RE.match(token):
        token = token.replace("D", "E").replace("d", "e")
        try:
            if re.match(r"^[+-]?\d+$", token):
                return int(token)
            return float(token)
        except ValueError:
            pass
    return token


def parse_hdus(blob: bytes, max_hdus: int = 8) -> list[dict[str, Any]]:
    """解析 MEF 的前若干个头。数据区按 NAXIS/BITPIX 计算字节数跳过。"""
    text = blob.decode("latin-1", "replace")
    n = len(text)
    pos = 0
    hdus: list[dict[str, Any]] = []

    while pos < n and len(hdus) < max_hdus:
        hdu: dict[str, Any] = {}
        header_start = pos
        found_end = False
        while pos + 2880 <= n:
            block = text[pos : pos + 2880]
            pos += 2880
            for j in range(0, 2880, 80):
                card = block[j : j + 80]
                key = card[:8].strip()
                if key == "END":
                    found_end = True
                    break
                if not key:
                    continue
                if key == "HIERARCH":
                    # HIERARCH 长关键字：`HIERARCH SGT_LAT_MIDPT = 63.1 / ...`
                    body = card[8:].strip()
                    m = re.match(r"([A-Za-z0-9_.\-]+)\s*=\s*(.*)$", body)
                    if m:
                        hdu[m.group(1)] = parse_value(m.group(2))
                    continue
                if card[8:10] == "= ":
                    hdu[key] = parse_value(card[10:])
            if found_end:
                break
        if not found_end:
            # 头缓存是 45 KB 的 Range 切片，最后一个头可能被截断；丢弃半截 HDU。
            break
        hdus.append(hdu)

        # 跳到数据区之后。头缓存通常在 IMAGE 数据开始处截断，下一次循环自然退出。
        naxis = int(hdu.get("NAXIS") or 0)
        if naxis <= 0:
            continue
        bitpix = abs(int(hdu.get("BITPIX") or 8))
        pcount = int(hdu.get("PCOUNT") or 0)
        gcount = int(hdu.get("GCOUNT") or 1)
        if str(hdu.get("XTENSION", "")).strip().upper() == "BINTABLE":
            nbytes = int(hdu.get("NAXIS1") or 0) * int(hdu.get("NAXIS2") or 0) + pcount * gcount
        else:
            elements = 1
            for d in range(1, naxis + 1):
                elements *= int(hdu.get(f"NAXIS{d}") or 0)
            nbytes = elements * bitpix // 8 + pcount * gcount
        if nbytes > 0:
            pos += ((nbytes + 2879) // 2880) * 2880
        if pos <= header_start:  # 防御：绝不原地打转
            break

    return hdus


def parse_header_file(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    return parse_hdus(Path(path).read_bytes())


def _pick_image_hdu(hdus: list[dict[str, Any]]) -> dict[str, Any]:
    for h in hdus:
        if str(h.get("EXTNAME", "")).strip().upper() == "IMAGE" and "OBSID" in h:
            return h
    for h in reversed(hdus):
        if "OBSID" in h:
            return h
    raise HeaderError("没有找到含 OBSID 的 HDU")


def _f(h: dict[str, Any], key: str, default: float | None = None) -> float | None:
    v = h.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(h: dict[str, Any], key: str, default: int | None = None) -> int | None:
    v = h.get(key)
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _s(h: dict[str, Any], key: str, default: str = "") -> str:
    v = h.get(key)
    return default if v is None else str(v).strip()


def _parse_obs_id(obs_id: str) -> tuple[str, int | None, int | None]:
    """`2025W48_1A_0658_4` -> (`2025W48_1A`, 658, 4)。"""
    parts = obs_id.rsplit("_", 2)
    if len(parts) != 3:
        return obs_id, None, None
    week, field, rep = parts
    try:
        return week, int(field), int(rep)
    except ValueError:
        return week, None, None


def pixel_scale_arcsec(h: dict[str, Any]) -> float | None:
    """由 PC/CD 矩阵求像素尺度（角秒/像素）。"""
    cdelt1 = _f(h, "CDELT1", 1.0) or 1.0
    cdelt2 = _f(h, "CDELT2", 1.0) or 1.0
    if "PC1_1" in h:
        det = (_f(h, "PC1_1", 0.0) or 0.0) * (_f(h, "PC2_2", 0.0) or 0.0) - (
            _f(h, "PC1_2", 0.0) or 0.0
        ) * (_f(h, "PC2_1", 0.0) or 0.0)
        return abs(det) ** 0.5 * abs(cdelt1 * cdelt2) * 3600.0
    if "CD1_1" in h:
        det = (_f(h, "CD1_1", 0.0) or 0.0) * (_f(h, "CD2_2", 0.0) or 0.0) - (
            _f(h, "CD1_2", 0.0) or 0.0
        ) * (_f(h, "CD2_1", 0.0) or 0.0)
        return abs(det) ** 0.5 * 3600.0
    return None


def extract_record(blob: bytes, source_path: str | None = None) -> dict[str, Any]:
    """从一份 FITS 头/切片中提取入库记录。

    必需：OBSID、DETECTOR、CRVAL1、CRVAL2、MJD-OBS（或 MJD-AVG）。
    """
    hdus = parse_hdus(blob)
    if not hdus:
        raise HeaderError("空头/无法解析")
    h = _pick_image_hdu(hdus)

    obs_id = _s(h, "OBSID")
    if not obs_id:
        raise HeaderError("OBSID 为空")
    detector = _i(h, "DETECTOR")
    if detector is None:
        raise HeaderError(f"{obs_id}: 没有 DETECTOR")
    ra = _f(h, "CRVAL1")
    dec = _f(h, "CRVAL2")
    if ra is None or dec is None:
        raise HeaderError(f"{obs_id}: 没有 CRVAL1/2")
    mjd = _f(h, "MJD-OBS") or _f(h, "MJD-AVG") or _f(h, "MJD-BEG")
    if mjd is None:
        raise HeaderError(f"{obs_id}: 没有 MJD-OBS/MJD-AVG")

    week, field_no, repeat_no = _parse_obs_id(obs_id)
    date_obs = _s(h, "DATE-OBS") or _s(h, "DATE-AVG")
    scale = pixel_scale_arcsec(h)

    return {
        "obs_id": obs_id,
        "detector": detector,
        "week": week,
        "field_no": field_no,
        "repeat_no": repeat_no,
        "ra": float(ra),
        "dec": float(dec),
        "mjd": float(mjd),
        "date_obs": date_obs,
        "xposure": _f(h, "XPOSURE"),
        "sgt_lat": _f(h, "SGT_LAT_MIDPT"),
        "sgt_lon": _f(h, "SGT_LON_MIDPT"),
        "elon": _f(h, "SPS_ELON"),
        "elat": _f(h, "SPS_ELAT"),
        "naxis1": _i(h, "NAXIS1"),
        "naxis2": _i(h, "NAXIS2"),
        "pixel_scale_arcsec": scale,
        "crpix1": _f(h, "CRPIX1"),
        "crpix2": _f(h, "CRPIX2"),
        "sip_order": _i(h, "A_ORDER"),
        "psf_fwhm_arcsec": _f(h, "PSF_FWHM"),
        "l2dqa": _s(h, "L2DQAFLG") or _s(h, "L1DQAFLG"),
        "source_path": source_path,
    }
