"""Conservative automatic route-geometry reconstruction via Project OSRM."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from .network import NetworkRequestError, fetch_bytes, read_json_cache, write_json_cache
from .track import track_distance_m


OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
CACHE_AGE_S = 7 * 24 * 60 * 60
MAX_ANCHORS = 100
MAX_OUTPUT_POINTS = 900
TARGET_POINT_SPACING_M = 150.0


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache" / "osrm-geometry"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "roadproof" / "osrm-geometry"


def _valid_redirect(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "router.project-osrm.org"


def _resample(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    distances = [track_distance_m([a, b]) for a, b in zip(points, points[1:])]
    total = sum(distances)
    output_count = max(2, min(MAX_OUTPUT_POINTS, math.ceil(total / TARGET_POINT_SPACING_M) + 1))
    if len(points) <= output_count:
        return points
    targets = [total * index / (output_count - 1) for index in range(output_count)]
    result = [points[0]]
    edge = 0
    elapsed = 0.0
    for target in targets[1:-1]:
        while edge < len(distances) - 1 and elapsed + distances[edge] < target:
            elapsed += distances[edge]
            edge += 1
        fraction = 0.0 if distances[edge] <= 0 else (target - elapsed) / distances[edge]
        start, end = points[edge], points[edge + 1]
        result.append((start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction))
    return [*result, points[-1]]


def reconstruct_maneuver_geometries(route: dict[str, Any], *, cache_dir: Path | None = None) -> tuple[dict[int, list[tuple[float, float]]], dict[str, Any]]:
    maneuvers = route.get("maneuvers") or []
    anchors = [(float(item["start_lat"]), float(item["start_lon"])) for item in maneuvers]
    if maneuvers:
        anchors.append((float(maneuvers[-1]["end_lat"]), float(maneuvers[-1]["end_lon"])))
    deduplicated = [point for index, point in enumerate(anchors) if index == 0 or point != anchors[index - 1]]
    if len(deduplicated) < 2 or len(deduplicated) > MAX_ANCHORS:
        return {}, {"status": "unavailable", "reason": f"automatic geometry requires 2-{MAX_ANCHORS} ordered Google maneuver anchors"}
    coordinates = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in deduplicated)
    url = f"{OSRM_ROUTE_URL}/{coordinates}?{urlencode({'overview': 'false', 'geometries': 'geojson', 'steps': 'true', 'alternatives': 'false', 'continue_straight': 'true'})}"
    key = hashlib.sha256(url.encode()).hexdigest()
    path = (cache_dir or _cache_root()) / f"{key}.json"
    value = read_json_cache(path, max_age_s=CACHE_AGE_S)
    if value is None:
        try:
            value = json.loads(fetch_bytes(url, headers={"User-Agent": "RoadProof/1.1"}, max_bytes=8_000_000, validate_final_url=_valid_redirect).decode())
        except (NetworkRequestError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {}, {"status": "unavailable", "reason": f"OSRM geometry reconstruction unavailable: {exc}"}
        write_json_cache(path, value)
    try:
        legs = value["routes"][0]["legs"]
    except (KeyError, IndexError, TypeError) as exc:
        return {}, {"status": "unavailable", "reason": f"OSRM returned unusable route geometry: {exc}"}
    accepted: dict[int, list[tuple[float, float]]] = {}
    rejected: list[dict[str, Any]] = []
    for sequence, (maneuver, leg) in enumerate(zip(maneuvers, legs)):
        points: list[tuple[float, float]] = []
        for step in leg.get("steps", []):
            for item in (step.get("geometry") or {}).get("coordinates", []):
                point = (float(item[1]), float(item[0]))
                if not points or point != points[-1]:
                    points.append(point)
        google_distance = float(maneuver["distance_m"])
        osrm_distance = float(leg.get("distance") or 0)
        difference = abs(google_distance - osrm_distance)
        tolerance = max(200.0, google_distance * 0.12)
        if len(points) >= 2 and difference <= tolerance:
            accepted[sequence] = _resample(points)
        else:
            rejected.append({"sequence": sequence, "difference_m": difference, "tolerance_m": tolerance})
    return accepted, {"status": "partial" if rejected else "accepted", "accepted": len(accepted), "rejected": rejected, "source": "Project OSRM per-maneuver reconstruction"}
