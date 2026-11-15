"""Python 客户端：本地直连或 HTTP 远程。

    from client import RemoteCatalog
    db = RemoteCatalog("http://127.0.0.1:8797")
    print(db.coverage(269.452077, 4.693365, 0.2))

也提供 `LocalCatalog`，适合离线批处理。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from db.catalog import Catalog
from db.config import DEFAULT_BLOBS, DEFAULT_DB


class RemoteCatalog:
    def __init__(self, base_url: str = "http://127.0.0.1:8797", *, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean)
        req = urllib.request.Request(url, headers={"User-Agent": "spherex-db-client/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get_bytes(self, path: str) -> bytes:
        req = urllib.request.Request(self.base_url + path, headers={"User-Agent": "spherex-db-client/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def health(self) -> dict:
        return self._get("/api/health")

    def stats(self) -> dict:
        return self._get("/api/stats")

    def targets(self) -> list[dict]:
        return self._get("/api/targets")["targets"]

    def target_epochs(self, target_id: str, limit: int = 500) -> dict:
        return self._get(f"/api/targets/{urllib.parse.quote(target_id)}/epochs", {"limit": limit})

    def epochs(self, ra: float, dec: float, fov: float = 0.2) -> dict:
        return self._get("/api/epochs", {"ra": ra, "dec": dec, "fov": fov})

    def epochs_v1(
        self,
        *,
        target_id: str | None = None,
        ra: float | None = None,
        dec: float | None = None,
        fov: float = 0.25,
        radius: float = 2.47,
        release: str | None = None,
        limit: int = 500,
        offset: int = 0,
        mjd_min: float | None = None,
        mjd_max: float | None = None,
    ) -> dict:
        return self._get(
            "/api/v1/epochs",
            {
                "target_id": target_id,
                "ra": ra,
                "dec": dec,
                "fov": fov,
                "radius": radius,
                "release": release,
                "limit": limit,
                "offset": offset,
                "mjd_min": mjd_min,
                "mjd_max": mjd_max,
            },
        )

    def coverage_v1(
        self,
        ra: float,
        dec: float,
        radius: float = 2.47,
        *,
        mjd_min: float | None = None,
        mjd_max: float | None = None,
    ) -> dict:
        return self._get(
            "/api/v1/coverage",
            {
                "ra": ra,
                "dec": dec,
                "radius": radius,
                "mjd_min": mjd_min,
                "mjd_max": mjd_max,
            },
        )

    def cone(
        self,
        ra: float,
        dec: float,
        radius: float = 0.2,
        *,
        limit: int = 100,
        offset: int = 0,
        order: str = "sep",
        detector: int | None = None,
        release: str | None = None,
        mjd_min: float | None = None,
        mjd_max: float | None = None,
        size: float = 0.2,
        cutouts: bool = False,
    ) -> dict:
        return self._get(
            "/api/cone",
            {
                "ra": ra,
                "dec": dec,
                "radius": radius,
                "limit": limit,
                "offset": offset,
                "order": order,
                "detector": detector,
                "release": release,
                "mjd_min": mjd_min,
                "mjd_max": mjd_max,
                "size": size,
                "cutouts": 1 if cutouts else 0,
            },
        )

    def coverage(
        self,
        ra: float,
        dec: float,
        radius: float = 0.2,
        *,
        mjd_min: float | None = None,
        mjd_max: float | None = None,
    ) -> dict:
        return self._get(
            "/api/coverage",
            {"ra": ra, "dec": dec, "radius": radius, "mjd_min": mjd_min, "mjd_max": mjd_max},
        )

    def frame(self, obs_id: str, detector: int) -> dict:
        return self._get(f"/api/frames/{urllib.parse.quote(obs_id)}/{int(detector)}")

    def weeks(self, release: str | None = None) -> dict:
        return self._get("/api/weeks", {"release": release})

    def cutouts(self, obs_id: str | None = None, frame_id: int | None = None) -> dict:
        return self._get("/api/cutouts", {"obs_id": obs_id, "frame_id": frame_id})

    def export(self, name: str) -> dict:
        return self._get(f"/api/export/{name}.json")

    def blob(self, sha256: str) -> bytes:
        return self._get_bytes(f"/api/blob/{sha256}")


class LocalCatalog:
    def __init__(self, db_path: str = str(DEFAULT_DB), blob_root: str = str(DEFAULT_BLOBS)):
        self._cat = Catalog(db_path, readonly=True, blob_root=blob_root)

    def __enter__(self) -> "LocalCatalog":
        return self

    def __exit__(self, *exc: object) -> None:
        self._cat.close()

    def __getattr__(self, item: str):
        return getattr(self._cat, item)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="数据库客户端快速自检")
    ap.add_argument("--url", default="http://127.0.0.1:8797")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.db:
        with LocalCatalog(args.db) as cat:
            print(json.dumps(cat.health(), ensure_ascii=False, indent=2))
    else:
        db = RemoteCatalog(args.url)
        print(json.dumps(db.health(), ensure_ascii=False, indent=2))
