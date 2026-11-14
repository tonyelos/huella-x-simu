"""最小但完整的 FITS 读取器（numpy only）。

支持：
- 多块 2880 字节头（SPHEREx 的 IMAGE 头有 9 块）
- PRIMARY / IMAGE / BINTABLE
- BITPIX 8/16/32/64/-32/-64
- 关键字值解析（字符串、布尔、整数、浮点）
- 从内存或文件读取

设计目标不是通用，而是**可审计**：每个偏移量都能对应 FITS 标准。
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import numpy as np

BLOCK = 2880
BITPIX_DTYPE = {
    8: ">u1",
    16: ">i2",
    32: ">i4",
    64: ">i8",
    -32: ">f4",
    -64: ">f8",
}


@dataclass
class HDU:
    header: dict
    data: np.ndarray | bytes | None
    kind: str  # PRIMARY / IMAGE / BINTABLE

    @property
    def name(self) -> str:
        return str(self.header.get("EXTNAME", "")).strip().strip("'").strip()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        shape = getattr(self.data, "shape", None)
        return f"<HDU {self.name or self.kind} {shape}>"


@dataclass
class FitsFile:
    hdus: list[HDU] = field(default_factory=list)

    def __getitem__(self, name: str) -> HDU:
        key = name.upper()
        for h in self.hdus:
            if h.name.upper() == key:
                return h
        raise KeyError(f"HDU {name!r} not found; have {[h.name for h in self.hdus]}")

    def get(self, name: str) -> HDU | None:
        try:
            return self[name]
        except KeyError:
            return None

    def primary_header(self) -> dict:
        return self.hdus[0].header if self.hdus else {}


def _parse_value(raw: str):
    """解析 FITS 卡片 '=' 右侧的值（已去掉注释）。"""
    s = raw.strip()
    if not s:
        return ""
    if s.startswith("'"):
        # 字符串：单引号内，双写 '' 表示一个引号
        out = []
        i = 1
        while i < len(s):
            ch = s[i]
            if ch == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(ch)
            i += 1
        return "".join(out).rstrip()
    if s[0] in "TF":
        return s[0] == "T"
    low = s.lower()
    if low in ("nan", "+nan", "-nan"):
        return float("nan")
    try:
        if any(c in s for c in ".eEdD"):
            return float(s.replace("D", "E").replace("d", "e"))
        return int(s)
    except ValueError:
        return s


def _parse_header(block: bytes, start: int) -> tuple[dict, int]:
    """从 start 开始读一个头，返回 (header, 头占用的字节数)。"""
    header: dict = {}
    nblocks = 0
    while True:
        chunk = block[start + nblocks * BLOCK : start + (nblocks + 1) * BLOCK]
        if len(chunk) < BLOCK:
            raise ValueError("FITS header truncated")
        text = chunk.decode("latin-1")
        for j in range(0, BLOCK, 80):
            card = text[j : j + 80]
            key = card[:8].strip()
            if not key:
                continue
            if key == "END":
                return header, (nblocks + 1) * BLOCK
            if card[8:10] == "= ":
                value = card[10:80].split("/")[0]
                header[key] = _parse_value(value)
            elif key == "HIERARCH":
                # HIERARCH 关键字：前 8 字符为 HIERARCH，键名在卡片中部
                body = card[9:].strip()
                if "=" in body:
                    k, v = body.split("=", 1)
                    header["HIERARCH " + k.strip()] = _parse_value(v.split("/")[0])
        nblocks += 1


def read_fits(source: str | bytes | io.BytesIO) -> FitsFile:
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as fh:
            blob = fh.read()
    elif isinstance(source, bytes):
        blob = source
    else:
        blob = source.read()

    out = FitsFile()
    offset = 0
    while offset + BLOCK <= len(blob):
        header, hlen = _parse_header(blob, offset)
        start = offset + hlen
        xt = str(header.get("XTENSION", "IMAGE")).strip().upper()
        naxis = int(header.get("NAXIS", 0))
        bitpix = int(header.get("BITPIX", 8))

        if xt == "BINTABLE":
            nbytes = int(header.get("NAXIS1", 0)) * int(header.get("NAXIS2", 0))
            data = blob[start : start + nbytes]
            kind = "BINTABLE"
        else:
            n = 0
            if naxis > 0:
                n = 1
                for d in range(1, naxis + 1):
                    n *= int(header.get(f"NAXIS{d}", 0))
            if n > 0:
                dtype = np.dtype(BITPIX_DTYPE[bitpix])
                dims = [int(header.get(f"NAXIS{d}", 1)) for d in range(naxis, 0, -1)]
                data = np.frombuffer(blob, dtype=dtype, count=n, offset=start).reshape(dims)
            else:
                data = None
            kind = "PRIMARY" if not xt or xt == "IMAGE" and offset == 0 and naxis == 0 else "IMAGE"
            nbytes = abs(bitpix) // 8 * n
        out.hdus.append(HDU(header=header, data=data, kind=kind))

        offset = start + (((nbytes + BLOCK - 1) // BLOCK) * BLOCK if nbytes else 0)

    return out


def header_float(header: dict, key: str, default=None):
    v = header.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def header_str(header: dict, key: str, default="") -> str:
    v = header.get(key, default)
    return str(v).strip().strip("'").strip() if v is not None else default


def read_wcs_wave(hdu: HDU) -> dict | None:
    """解析 WCS-WAVE BINTABLE：X(nx), Y(ny), VALUES(2,nx,ny) = (λ, Δλ) µm。"""
    if hdu is None or hdu.kind != "BINTABLE":
        return None
    h = hdu.header
    try:
        tdim1 = str(h.get("TDIM1", "")).strip().strip("'").strip()
        tdim2 = str(h.get("TDIM2", "")).strip().strip("'").strip()
        nx = int(tdim1.strip("()"))
        ny = int(tdim2.strip("()"))
    except (TypeError, ValueError):
        return None
    raw = hdu.data
    if raw is None:
        return None
    nbytes = len(raw)
    x = np.frombuffer(raw, dtype=">i4", count=nx, offset=0).astype(int)
    y = np.frombuffer(raw, dtype=">i4", count=ny, offset=nx * 4).astype(int)
    off = nx * 4 + ny * 4
    nval = nx * ny * 2
    if off + nval * 4 > nbytes:
        return None
    vals = np.frombuffer(raw, dtype=">f4", count=nval, offset=off).astype(float)
    # TDIM3 = (2, nx, ny)，Fortran 顺序：2 最快
    cube = vals.reshape(ny, nx, 2)
    return {
        "x": x,
        "y": y,
        "wavelength": cube[:, :, 0],  # µm
        "bandwidth": cube[:, :, 1],
        "detector": header_str(h, "DETECTOR"),
    }


def read_epsf(hdu: HDU) -> dict | None:
    """解析 EPSF BINTABLE：441 行 × (BINX,BINY,XCENTER,YCENTER,XWIDTH,YWIDTH,
    NSTAR,NEFF_MEAN,CWAVE,CBAND,EPSF[33,33])。"""
    if hdu is None or hdu.kind != "BINTABLE":
        return None
    h = hdu.header
    try:
        nrows = int(h.get("NAXIS2", 0))
        rowlen = int(h.get("NAXIS1", 0))
    except (TypeError, ValueError):
        return None
    raw = hdu.data or b""
    if nrows <= 0 or len(raw) < nrows * rowlen:
        return None

    fields = []
    for i in range(1, int(h.get("TFIELDS", 0)) + 1):
        ttype = str(h.get(f"TTYPE{i}", "")).strip().strip("'").strip()
        tform = str(h.get(f"TFORM{i}", "")).strip().strip("'").strip()
        if ttype:
            fields.append((ttype, tform))

    # 前 10 列是标量，第 11 列是 1089D 数组
    rows = {
        "BINX": np.zeros(nrows, int),
        "BINY": np.zeros(nrows, int),
        "XCENTER": np.zeros(nrows),
        "YCENTER": np.zeros(nrows),
        "NSTAR": np.zeros(nrows, int),
        "CWAVE": np.zeros(nrows),
        "CBAND": np.zeros(nrows),
        "EPSF": np.zeros((nrows, 33, 33)),
    }
    # 约定前 10 个标量字段顺序与 TFORM 一致；用固定布局解析（SPHEREx 定义）
    off = 0
    for r in range(nrows):
        base = r * rowlen
        binx = np.frombuffer(raw, dtype=">i4", count=1, offset=base + 0)[0]
        biny = np.frombuffer(raw, dtype=">i4", count=1, offset=base + 4)[0]
        xc = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 8)[0]
        yc = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 12)[0]
        _xw = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 16)[0]
        _yw = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 20)[0]
        nst = np.frombuffer(raw, dtype=">i4", count=1, offset=base + 24)[0]
        _neff = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 28)[0]
        cw = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 32)[0]
        cb = np.frombuffer(raw, dtype=">f4", count=1, offset=base + 36)[0]
        rows["BINX"][r] = binx
        rows["BINY"][r] = biny
        rows["XCENTER"][r] = xc
        rows["YCENTER"][r] = yc
        rows["NSTAR"][r] = nst
        rows["CWAVE"][r] = cw
        rows["CBAND"][r] = cb
        arr = np.frombuffer(raw, dtype=">f8", count=33 * 33, offset=base + 40)
        rows["EPSF"][r] = arr.reshape(33, 33)
    rows["fields"] = fields
    return rows


def fits_to_npz(fits: FitsFile, ra: float, dec: float, size_deg: float) -> dict:
    """把一次切片压成前端/流水线需要的精简字典（丢弃 EPSF）。"""
    image_hdu = fits.get("IMAGE")
    if image_hdu is None:
        raise ValueError("no IMAGE HDU")
    out = {
        "ra": ra,
        "dec": dec,
        "size_deg": size_deg,
        "header": {k: v for k, v in image_hdu.header.items() if not k.startswith("MSK")},
        "image": np.asarray(image_hdu.data, dtype=np.float64),
    }
    for name in ("FLAGS", "VARIANCE", "ZODI"):
        hdu = fits.get(name)
        out[name.lower()] = np.asarray(hdu.data) if hdu is not None else None
    wave = read_wcs_wave(fits.get("WCS-WAVE"))
    out["wave_grid"] = wave
    out["obsid"] = header_str(image_hdu.header, "OBSID")
    out["detector"] = header_str(image_hdu.header, "DETECTOR")
    out["mjd"] = header_float(image_hdu.header, "MJD-OBS")
    out["date_obs"] = header_str(image_hdu.header, "DATE-OBS")
    out["bunit"] = header_str(image_hdu.header, "BUNIT")
    return out
