"""Sweden adapter backed by Trafikverket's public NVDB map service."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlencode

from .network import NetworkRequestError, fetch_bytes, read_json_cache, write_json_cache
from .sampled import OfficialAdapterError, PointEvidence, analyze_sampled_route


ADAPTER_VERSION = "se-nvdb-point-1"
WMS_ENDPOINT = "https://geo-netinfo.trafikverket.se/MapService/wms.axd/NetInfo_1_10"
WMS_CAPABILITIES = f"{WMS_ENDPOINT}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
NVDB_PAGE = "https://bransch.trafikverket.se/tjanster/data-kartor-och-geodatatjanster/las-om-vara-data/vagdata/"
LAYERS = ("Vagtrafiknat", "Motorvag", "Motortrafikled", "TattbebyggtOmrade")
CACHE_MAX_AGE_S = 7 * 24 * 60 * 60


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache" / "sweden-nvdb"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "roadproof" / "sweden-nvdb"


class SwedenPointClassifier:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or _cache_root()
        self._memory: dict[tuple[float, float], PointEvidence] = {}
        self._lock = threading.Lock()

    def _request(self, lat: float, lon: float) -> dict[str, Any]:
        latitude_radius = 180.0 / 111_320.0
        longitude_radius = latitude_radius / max(0.2, math.cos(math.radians(lat)))
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "LAYERS": ",".join(LAYERS),
            "QUERY_LAYERS": ",".join(LAYERS),
            "CRS": "EPSG:4326",
            # WMS 1.3.0 uses latitude,longitude axis order for EPSG:4326.
            "BBOX": (
                f"{lat-latitude_radius:.7f},{lon-longitude_radius:.7f},"
                f"{lat+latitude_radius:.7f},{lon+longitude_radius:.7f}"
            ),
            "WIDTH": "101",
            "HEIGHT": "101",
            "I": "50",
            "J": "50",
            "INFO_FORMAT": "application/json",
            "FEATURE_COUNT": "100",
        }
        query = urlencode(params)
        cache_path = self.cache_dir / f"{ADAPTER_VERSION}-{hashlib.sha256(query.encode()).hexdigest()}.json"
        cached = read_json_cache(cache_path, max_age_s=CACHE_MAX_AGE_S)
        if cached is not None and isinstance(cached.get("features"), list):
            return cached

        url = f"{WMS_ENDPOINT}?{query}"
        try:
            body = fetch_bytes(
                url,
                headers={"User-Agent": "RoadProof/0.6", "Accept": "application/json"},
                max_bytes=16 * 1024 * 1024,
                validate_final_url=lambda final: final.split("?", 1)[0] == WMS_ENDPOINT,
            )
            value = json.loads(body)
        except (NetworkRequestError, json.JSONDecodeError) as exc:
            raise OfficialAdapterError(f"Could not read the official Trafikverket NVDB WMS: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("features"), list):
            raise OfficialAdapterError("The official Trafikverket WMS returned an unrecognized response.")
        write_json_cache(cache_path, value)
        return value

    def __call__(self, lat: float, lon: float) -> PointEvidence:
        key = (round(lat, 7), round(lon, 7))
        with self._lock:
            cached = self._memory.get(key)
        if cached is not None:
            return cached
        value = self._request(lat, lon)
        found = {
            str(feature.get("id") or "").split(".", 1)[0]
            for feature in value["features"]
        }
        if "Motorvag" in found or "Motortrafikled" in found:
            result = PointEvidence("Highway", "NVDB Motorvag / Motortrafikled", ", ".join(sorted(found)))
        elif "Vagtrafiknat" in found and "TattbebyggtOmrade" in found:
            result = PointEvidence("City", "NVDB Vagtrafiknat + TattbebyggtOmrade", ", ".join(sorted(found)))
        elif "Vagtrafiknat" in found:
            result = PointEvidence("Country", "NVDB Vagtrafiknat outside TattbebyggtOmrade", ", ".join(sorted(found)))
        else:
            result = PointEvidence("Unresolved", "NVDB WMS", "no official motor-road feature at the sample pixel")
        with self._lock:
            self._memory[key] = result
        return result


def analyze_sweden_route(
    route: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    progress=None,
    classifier=None,
) -> dict[str, Any] | None:
    point_classifier = classifier or SwedenPointClassifier(cache_dir)
    return analyze_sampled_route(
        route,
        country="SE",
        country_name="Swedish",
        adapter=f"Trafikverket NVDB WMS adapter {ADAPTER_VERSION}",
        classifier=point_classifier,
        official_layers={
            "Highway": "NVDB Motorvag / Motortrafikled",
            "Country": "NVDB Vagtrafiknat outside TattbebyggtOmrade",
            "City": "NVDB Vagtrafiknat within TattbebyggtOmrade",
            "Unresolved": "Maneuvers that failed chord/sample reconciliation",
        },
        official_sources=[
            f"Trafikverket road-data description: {NVDB_PAGE}",
            f"Trafikverket public NVDB map-service capabilities: {WMS_CAPABILITIES}",
            "Queried layers: Vagtrafiknat, Motorvag, Motortrafikled, TattbebyggtOmrade.",
        ],
        mapping_note=(
            "Operational mapping: NVDB Motorvag/Motortrafikled = Highway; motor-road samples "
            "with/without NVDB TattbebyggtOmrade = City/Country."
        ),
        progress=progress,
    )
