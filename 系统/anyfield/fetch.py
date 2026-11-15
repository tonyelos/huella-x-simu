"""Allowlisted on-demand cutout fetch for C-P1B.

Only a server-constructed IRSA static-cutout URL is fetched. The caller supplies
an S3 key + obsid/detector + RA/Dec/FOV; it cannot supply a URL. Default hosts
are the IRSA static cutout host and the public SPHEREx S3 host. Tests may pass
a local fake host via ``allow_hosts``; production keeps the strict default.

No SIM, no arbitrary SSRF, no redirects to another host, and a hard byte cap.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

DEFAULT_IRSA_BASE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
DEFAULT_ALLOW_HOSTS = ("irsa.ipac.caltech.edu", "nasa-irsa-spherex.s3.amazonaws.com")
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
_KEY_RE = re.compile(r"^(dr1|qr3)/level2/[^/]+/[^/]+/[1-6]/[^/]+\.fits$")


class FetchError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.retryable = bool(retryable)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise FetchError("FETCH_REDIRECT_DENIED", f"不允许重定向：{newurl}", retryable=False)


def validate_s3_key(key: str) -> tuple[str, str, str]:
    """Return (release, week, version) from a safe SPHEREx level2 key."""
    key = str(key or "").strip()
    if key.startswith(("http://", "https://", "file://")):
        raise FetchError("NETWORK_SOURCE_NOT_ALLOWED", "s3_key 必须是对象键，不是 URL", retryable=False)
    if ".." in key.split("/") or not _KEY_RE.match(key):
        raise FetchError("INVALID_S3_KEY", "s3_key 必须是 dr1|qr3/level2/.../*.fits", retryable=False)
    parts = key.split("/")
    return parts[0], parts[2], parts[3]


def validate_obsid_key(key: str, obsid: str, detector: int) -> None:
    obsid = str(obsid or "").strip()
    if not obsid or "/" in obsid or "\x00" in obsid:
        raise FetchError("INVALID_REQUEST", "obsid 非法", retryable=False)
    try:
        detector_i = int(detector)
    except (TypeError, ValueError) as exc:
        raise FetchError("INVALID_REQUEST", "detector 必须是 1–6", retryable=False) from exc
    if not (1 <= detector_i <= 6):
        raise FetchError("INVALID_REQUEST", "detector 必须是 1–6", retryable=False)
    name = key.rsplit("/", 1)[-1]
    marker = f"level2_{obsid}D{detector_i}_spx_"
    if marker not in name:
        raise FetchError("OBSID_DETECTOR_MISMATCH", "obsid/detector 与 s3_key 文件名不一致", retryable=False)


class FetchClient:
    def __init__(
        self,
        *,
        irsa_base: str = DEFAULT_IRSA_BASE,
        allow_hosts: Iterable[str] = DEFAULT_ALLOW_HOSTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.irsa_base = str(irsa_base).rstrip("/") + "/"
        self.allow_hosts = tuple(str(h).strip().lower() for h in allow_hosts if str(h).strip())
        self.max_bytes = int(max_bytes)
        self.timeout = float(timeout)
        self._opener = urllib.request.build_opener(_NoRedirect)

    def cutout_url(self, key: str, ra: float, dec: float, fov: float) -> str:
        validate_s3_key(key)
        # Keep the release prefix: IRSA path is <base>/<dr1|qr3>/level2/...
        url = f"{self.irsa_base}{key}?center={float(ra):.6f},{float(dec):.6f}d&size={float(fov):g}"
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in self.allow_hosts:
            raise FetchError(
                "FETCH_HOST_NOT_ALLOWED",
                f"fetch host 不在 allowlist：{host}",
                retryable=False,
            )
        return url

    def download(self, url: str) -> bytes:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in self.allow_hosts:
            raise FetchError("FETCH_HOST_NOT_ALLOWED", f"fetch host 不在 allowlist：{host}", retryable=False)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "MoveScope-anyfield-p1b/0.1", "Accept": "application/fits"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                final = urllib.parse.urlparse(response.geturl())
                if (final.hostname or "").lower() not in self.allow_hosts:
                    raise FetchError("FETCH_HOST_NOT_ALLOWED", "重定向目标不在 allowlist", retryable=False)
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        if int(length) > self.max_bytes:
                            raise FetchError("FETCH_TOO_LARGE", f"cutout 超过 {self.max_bytes} 字节", retryable=False)
                    except ValueError:
                        pass
                data = response.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise FetchError("FETCH_TOO_LARGE", f"cutout 超过 {self.max_bytes} 字节", retryable=False)
                if not data:
                    raise FetchError("FETCH_EMPTY", "cutout 为空", retryable=True)
                return data
        except FetchError:
            raise
        except urllib.error.HTTPError as exc:
            raise FetchError("UPSTREAM_UNAVAILABLE", f"IRSA cutout HTTP {exc.code}", retryable=True) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError("UPSTREAM_UNAVAILABLE", f"IRSA cutout 不可达/超时：{exc}", retryable=True) from exc
