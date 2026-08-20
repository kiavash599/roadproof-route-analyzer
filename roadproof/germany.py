"""Germany adapter backed by the official basemap.de vector tiles."""

from __future__ import annotations

import math
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .sampled import OfficialAdapterError, PointEvidence, analyze_sampled_route


ADAPTER_VERSION = "de-basemap-point-1"
TILE_ZOOM = 15
TILE_TEMPLATE = "https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/tiles/v2/bm_web_de_3857/{z}/{x}/{y}.pbf"
TILE_METADATA = "https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/tiles/v2/bm_web_de_3857/bm_web_de_3857.json"
PRODUCT_PAGE = "https://basemap.de/produkte-und-dienste/web-vektor/"
CACHE_MAX_AGE_S = 7 * 24 * 60 * 60
ROAD_SNAP_MAX_M = 90.0


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache" / "germany-basemap"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "roadproof" / "germany-basemap"


def _tile_coordinates(lat: float, lon: float, zoom: int) -> tuple[int, int, float, float]:
    scale = 2**zoom
    global_x = (lon + 180.0) / 360.0 * scale
    clipped_lat = max(-85.05112878, min(85.05112878, lat))
    latitude = math.radians(clipped_lat)
    global_y = (1.0 - math.asinh(math.tan(latitude)) / math.pi) / 2.0 * scale
    tile_x = math.floor(global_x)
    tile_y = math.floor(global_y)
    return tile_x, tile_y, global_x - tile_x, global_y - tile_y


def _download_tile(zoom: int, tile_x: int, tile_y: int, cache_dir: Path | None) -> bytes:
    path = (cache_dir or _cache_root()) / str(zoom) / str(tile_x) / f"{tile_y}.pbf"
    try:
        if time.time() - path.stat().st_mtime <= CACHE_MAX_AGE_S:
            return path.read_bytes()
    except OSError:
        pass

    url = TILE_TEMPLATE.format(z=zoom, x=tile_x, y=tile_y)
    request = Request(url, headers={"User-Agent": "RoadProof/0.5", "Accept": "application/x-protobuf"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                if response.geturl() != url:
                    raise OfficialAdapterError("The official basemap.de tile request redirected unexpectedly.")
                body = response.read()
            if not body:
                raise OfficialAdapterError("The official basemap.de tile was empty.")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                temporary.write_bytes(body)
                temporary.replace(path)
            except OSError:
                pass
            return body
        except (HTTPError, URLError, TimeoutError, OSError, OfficialAdapterError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise OfficialAdapterError(f"Could not read the official basemap.de vector tile: {last_error}")


def _segments(geometry: dict[str, Any]) -> list[tuple[list[float], list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    lines: list[list[list[float]]] = []
    if geometry_type == "LineString" and isinstance(coordinates, list):
        lines = [coordinates]
    elif geometry_type == "MultiLineString" and isinstance(coordinates, list):
        lines = [line for line in coordinates if isinstance(line, list)]
    return [
        (first, second)
        for line in lines
        for first, second in zip(line, line[1:])
        if isinstance(first, list) and isinstance(second, list) and len(first) >= 2 and len(second) >= 2
    ]


def _point_segment_distance(point: tuple[float, float], first: list[float], second: list[float]) -> float:
    px, py = point
    x1, y1 = float(first[0]), float(first[1])
    x2, y2 = float(second[0]), float(second[1])
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    ratio = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + ratio * dx), py - (y1 + ratio * dy))


def _point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    if len(ring) < 3:
        return False
    x, y = point
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def _point_in_geometry(point: tuple[float, float], geometry: dict[str, Any]) -> bool:
    coordinates = geometry.get("coordinates")
    polygons: list[list[list[list[float]]]] = []
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        polygons = [coordinates]
    elif geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        polygons = [polygon for polygon in coordinates if isinstance(polygon, list)]
    for polygon in polygons:
        if (
            polygon
            and _point_in_ring(point, polygon[0])
            and not any(_point_in_ring(point, hole) for hole in polygon[1:])
        ):
            return True
    return False


def _point_polygon_distance(point: tuple[float, float], geometry: dict[str, Any]) -> float:
    if _point_in_geometry(point, geometry):
        return 0.0
    coordinates = geometry.get("coordinates")
    polygons: list[list[list[list[float]]]] = []
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        polygons = [coordinates]
    elif geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        polygons = [polygon for polygon in coordinates if isinstance(polygon, list)]
    return min(
        (
            _point_segment_distance(point, first, second)
            for polygon in polygons
            for ring in polygon
            for first, second in zip(ring, ring[1:])
        ),
        default=math.inf,
    )


def classify_basemap_properties(properties: dict[str, Any], *, in_settlement: bool) -> PointEvidence:
    road_class = str(properties.get("klasse") or "").strip()
    road_class_folded = road_class.casefold()
    carriageway = str(properties.get("fahrbahn") or "").strip().casefold()
    if road_class_folded in {"bundesautobahn", "autobahn"} or carriageway == "getrennt":
        return PointEvidence(
            "Highway",
            "basemap.de Verkehrslinie",
            f"klasse={road_class}; fahrbahn={carriageway or 'not set'}",
        )
    if in_settlement:
        return PointEvidence("City", "basemap.de Verkehrslinie + Siedlungsflaeche", f"klasse={road_class}")
    return PointEvidence(
        "Country",
        "basemap.de Verkehrslinie beyond Siedlungsflaeche proximity",
        f"klasse={road_class}",
    )


class GermanyPointClassifier:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir
        self._decoded: dict[tuple[int, int], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _tile(self, tile_x: int, tile_y: int) -> dict[str, Any]:
        key = (tile_x, tile_y)
        with self._lock:
            cached = self._decoded.get(key)
        if cached is not None:
            return cached
        try:
            import mapbox_vector_tile
        except ImportError as exc:  # pragma: no cover - installer supplies it
            raise OfficialAdapterError("mapbox-vector-tile is missing; rerun the platform installer.") from exc
        try:
            decoded = mapbox_vector_tile.decode(_download_tile(TILE_ZOOM, tile_x, tile_y, self.cache_dir))
        except Exception as exc:
            if isinstance(exc, OfficialAdapterError):
                raise
            raise OfficialAdapterError(f"Could not decode the official basemap.de vector tile: {exc}") from exc
        with self._lock:
            self._decoded[key] = decoded
        return decoded

    def __call__(self, lat: float, lon: float) -> PointEvidence:
        tile_x, tile_y, fraction_x, fraction_y = _tile_coordinates(lat, lon, TILE_ZOOM)
        tile = self._tile(tile_x, tile_y)
        road_layer = tile.get("Verkehrslinie") or {}
        extent = float(road_layer.get("extent") or 4096)
        point = (fraction_x * extent, (1.0 - fraction_y) * extent)
        metres_per_unit = math.cos(math.radians(lat)) * 2 * math.pi * 6_378_137 / (2**TILE_ZOOM * extent)

        settlement_distance = min(
            (
                _point_polygon_distance(point, feature.get("geometry") or {}) * metres_per_unit
                for feature in (tile.get("Siedlungsflaeche") or {}).get("features", [])
            ),
            default=math.inf,
        )
        # Settlement polygons stop at the road surface. A short proximity
        # allowance keeps the road centreline in the same official built-up
        # context without turning distant rural roads into urban evidence.
        settlement = settlement_distance <= 120.0
        nearest: tuple[float, dict[str, Any]] | None = None
        for feature in road_layer.get("features", []):
            properties = feature.get("properties") or {}
            road_class = str(properties.get("klasse") or "").casefold()
            if "strasse" not in road_class and road_class not in {"bundesautobahn", "autobahn"}:
                continue
            if "nicht öffentliche" in road_class:
                continue
            distances = [
                _point_segment_distance(point, first, second)
                for first, second in _segments(feature.get("geometry") or {})
            ]
            if not distances:
                continue
            distance_m = min(distances) * metres_per_unit
            if nearest is None or distance_m < nearest[0]:
                nearest = (distance_m, properties)
        if nearest is None or nearest[0] > ROAD_SNAP_MAX_M:
            return PointEvidence("Unresolved", "basemap.de Verkehrslinie", "no official motor-road line within 90 m")
        return classify_basemap_properties(nearest[1], in_settlement=settlement)


def analyze_germany_route(
    route: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    progress=None,
    classifier=None,
) -> dict[str, Any] | None:
    point_classifier = classifier or GermanyPointClassifier(cache_dir)
    return analyze_sampled_route(
        route,
        country="DE",
        country_name="German",
        adapter=f"GeoBasis-DE/BKG basemap.de adapter {ADAPTER_VERSION}",
        classifier=point_classifier,
        official_layers={
            "Highway": "basemap.de Verkehrslinie: Bundesautobahn or separated carriageway",
            "Country": "basemap.de Verkehrslinie beyond 120 m of official Siedlungsflaeche",
            "City": "basemap.de Verkehrslinie in/near official Siedlungsflaeche",
            "Unresolved": "Maneuvers that failed chord/sample reconciliation",
        },
        official_sources=[
            f"GeoBasis-DE/BKG basemap.de Web Vektor product: {PRODUCT_PAGE}",
            f"Official tile metadata: {TILE_METADATA}",
            "Attribution: © GeoBasis-DE / BKG (2026), CC BY 4.0.",
        ],
        mapping_note=(
            "Operational mapping: Bundesautobahn and officially separated carriageways = Highway; "
            "other public road lines in/near or beyond 120 m from official Siedlungsflaeche = City/Country."
        ),
        progress=progress,
    )
