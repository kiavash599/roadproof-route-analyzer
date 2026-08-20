"""Operational diagnostics for adapters and local official-data caches."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from .adapters import SUPPORTED_COUNTRIES


DIAGNOSTIC_SCHEMA = "roadproof.diagnostic.v1"
CACHE_MAX_AGE_S = 7 * 24 * 60 * 60
ADAPTER_DETAILS = {
    "BE": ("be-regional-point-1", "Flanders Wegenregister, Brussels UrbIS, Wallonia PICC"),
    "DE": ("de-basemap-point-1", "GeoBasis-DE/BKG basemap.de Web Vektor"),
    "DK": ("dk-official-runtime-1", "Vejdirektoratet Vejman and Plandata"),
    "SE": ("se-nvdb-point-1", "Trafikverket NVDB map service"),
}


def cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "roadproof"


def adapter_document() -> dict[str, Any]:
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "kind": "adapters",
        "adapters": [
            {
                "country": country,
                "country_name": SUPPORTED_COUNTRIES[country],
                "adapter_version": ADAPTER_DETAILS[country][0],
                "official_source": ADAPTER_DETAILS[country][1],
                "single_country_runtime": True,
                "cross_border": False,
            }
            for country in sorted(SUPPORTED_COUNTRIES)
        ],
    }


def cache_document() -> dict[str, Any]:
    root = cache_root()
    now = time.time()
    files = []
    if root.is_dir():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            try:
                stat = path.stat()
                relative = str(path.relative_to(root))
            except OSError:
                continue
            corrupt = False
            if path.suffix.casefold() == ".json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    corrupt = True
            files.append(
                {
                    "path": relative,
                    "size_bytes": stat.st_size,
                    "age_seconds": max(0, round(now - stat.st_mtime)),
                    "stale": now - stat.st_mtime > CACHE_MAX_AGE_S,
                    "corrupt": corrupt,
                }
            )
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "kind": "cache",
        "inspected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "exists": root.is_dir(),
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "stale_count": sum(bool(item["stale"]) for item in files),
        "corrupt_count": sum(bool(item["corrupt"]) for item in files),
        "files": files,
    }
