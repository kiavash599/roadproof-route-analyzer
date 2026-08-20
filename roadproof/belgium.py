"""Regional Belgium adapter using official Flemish, Walloon and Brussels data."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Iterable
from urllib.parse import urlencode

from .network import NetworkRequestError, fetch_bytes, read_json_cache, write_json_cache
from .sampled import OfficialAdapterError, PointEvidence, analyze_sampled_route


ADAPTER_VERSION = "be-regional-point-1"
FLANDERS_ROAD_WFS = "https://geo.api.vlaanderen.be/Wegenregister/wfs"
FLANDERS_ROAD_LAYER = "Wegenregister:Wegsegment"
FLANDERS_ZONE_WFS = "https://opendata.apps.mow.vlaanderen.be/opendata-geoserver/awv/wfs"
FLANDERS_ZONE_LAYER = "awv:Afgeleide_zones_bebouwdekom"
WALLONIA_ROAD_QUERY = "https://geoservices.wallonie.be/arcgis/rest/services/TOPOGRAPHIE/PICC_VDIFF/MapServer/21/query"
WALLONIA_CATALOG = "https://geoportail.wallonie.be/catalogue/d26f16df-5326-4cd7-b768-709e75a25507.html"
BRUSSELS_WFS = "https://data.mobility.brussels/geoserver/bm_urbis/ows"
BRUSSELS_ROAD_LAYER = "bm_urbis:urbadm_ss"
CACHE_MAX_AGE_S = 7 * 24 * 60 * 60
ROAD_SNAP_MAX_M = 90.0


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache" / "belgium-official"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "roadproof" / "belgium-official"


def _iter_lines(geometry: dict[str, Any] | None) -> Iterable[list[list[float]]]:
    if not geometry:
        return
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "LineString" and isinstance(coordinates, list):
        yield coordinates
    elif geometry.get("type") == "MultiLineString" and isinstance(coordinates, list):
        for line in coordinates:
            if isinstance(line, list):
                yield line


def _point_segment_distance_m(lat: float, lon: float, first: list[float], second: list[float]) -> float:
    mean_latitude = math.radians(lat)
    scale_x = 111_320.0 * max(0.2, math.cos(mean_latitude))
    scale_y = 111_320.0
    x1, y1 = (float(first[0]) - lon) * scale_x, (float(first[1]) - lat) * scale_y
    x2, y2 = (float(second[0]) - lon) * scale_x, (float(second[1]) - lat) * scale_y
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x1, y1)
    ratio = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
    return math.hypot(x1 + ratio * dx, y1 + ratio * dy)


def _line_distance_m(lat: float, lon: float, geometry: dict[str, Any] | None) -> float:
    distances = [
        _point_segment_distance_m(lat, lon, first, second)
        for line in _iter_lines(geometry)
        for first, second in zip(line, line[1:])
        if isinstance(first, list) and isinstance(second, list) and len(first) >= 2 and len(second) >= 2
    ]
    return min(distances, default=math.inf)


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    if len(ring) < 3:
        return False
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def _point_in_geometry(lon: float, lat: float, geometry: dict[str, Any] | None) -> bool:
    if not geometry:
        return False
    coordinates = geometry.get("coordinates")
    polygons: list[list[list[list[float]]]] = []
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        polygons = [coordinates]
    elif geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        polygons = [polygon for polygon in coordinates if isinstance(polygon, list)]
    return any(
        polygon
        and _point_in_ring(lon, lat, polygon[0])
        and not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:])
        for polygon in polygons
    )


def classify_flanders_properties(properties: dict[str, Any], *, built_up: bool) -> PointEvidence:
    morphology = str(properties.get("morfologischeWegklasse") or "").strip().casefold()
    if morphology in {"autosnelweg", "weg met gescheiden rijbanen die geen autosnelweg is"}:
        return PointEvidence("Highway", "Wegenregister morfologischeWegklasse", morphology)
    if built_up:
        return PointEvidence("City", "Wegenregister + Afgeleide zones bebouwde kom", morphology)
    return PointEvidence("Country", "Wegenregister outside Afgeleide zones bebouwde kom", morphology)


class BelgiumPointClassifier:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or _cache_root()
        self._memory: dict[tuple[float, float], PointEvidence] = {}
        self._lock = threading.Lock()

    def _request_json(self, endpoint: str, params: dict[str, str], service_name: str) -> dict[str, Any]:
        query = urlencode(params)
        cache_key = hashlib.sha256((endpoint + "?" + query).encode()).hexdigest()
        cache_path = self.cache_dir / f"{ADAPTER_VERSION}-{cache_key}.json"
        cached = read_json_cache(cache_path, max_age_s=CACHE_MAX_AGE_S)
        if cached is not None and isinstance(cached.get("features"), list):
            return cached
        url = f"{endpoint}?{query}"
        try:
            body = fetch_bytes(
                url,
                headers={"User-Agent": "RoadProof/0.6", "Accept": "application/json"},
                max_bytes=32 * 1024 * 1024,
                validate_final_url=lambda final: final.split("?", 1)[0] == endpoint,
            )
            value = json.loads(body)
        except (NetworkRequestError, json.JSONDecodeError) as exc:
            raise OfficialAdapterError(f"Could not read {service_name}: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("features"), list):
            raise OfficialAdapterError(f"{service_name} returned an unrecognized response.")
        write_json_cache(cache_path, value)
        return value

    @staticmethod
    def _bbox(lat: float, lon: float, radius_m: float = 180.0) -> tuple[float, float, float, float]:
        latitude_radius = radius_m / 111_320.0
        longitude_radius = latitude_radius / max(0.2, math.cos(math.radians(lat)))
        return lon - longitude_radius, lat - latitude_radius, lon + longitude_radius, lat + latitude_radius

    def _wfs(
        self,
        endpoint: str,
        layer: str,
        lat: float,
        lon: float,
        service_name: str,
        *,
        count: int = 200,
    ) -> dict[str, Any]:
        box = self._bbox(lat, lon)
        return self._request_json(
            endpoint,
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": layer,
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "bbox": ",".join(f"{value:.7f}" for value in box) + ",EPSG:4326",
                "count": str(count),
            },
            service_name,
        )

    def _flanders(self, lat: float, lon: float) -> PointEvidence | None:
        roads = self._wfs(FLANDERS_ROAD_WFS, FLANDERS_ROAD_LAYER, lat, lon, "the official Flemish Wegenregister WFS")
        nearest: tuple[float, dict[str, Any]] | None = None
        for feature in roads["features"]:
            properties = feature.get("properties") or {}
            status = str(properties.get("wegsegmentstatus") or "").casefold()
            morphology = str(properties.get("morfologischeWegklasse") or "").casefold()
            restriction = str(properties.get("toegangsbeperking") or "").casefold()
            if status and status != "in gebruik":
                continue
            if any(value in morphology for value in ("wandel", "fiets", "tram", "dienstweg")):
                continue
            if "niet toegankelijk" in restriction:
                continue
            distance = _line_distance_m(lat, lon, feature.get("geometry"))
            if nearest is None or distance < nearest[0]:
                nearest = (distance, properties)
        if nearest is None or nearest[0] > ROAD_SNAP_MAX_M:
            return None
        morphology = str(nearest[1].get("morfologischeWegklasse") or "").strip().casefold()
        if morphology in {"autosnelweg", "weg met gescheiden rijbanen die geen autosnelweg is"}:
            return classify_flanders_properties(nearest[1], built_up=False)

        zones = self._wfs(FLANDERS_ZONE_WFS, FLANDERS_ZONE_LAYER, lat, lon, "the official Flemish built-up-zone WFS")
        # This source's EPSG:4326 output is rounded to 0.001 degrees. A 140 m
        # tolerance is therefore used only after an exact Wegenregister road snap.
        built_up = any(_line_distance_m(lat, lon, feature.get("geometry")) <= 140.0 for feature in zones["features"])
        return classify_flanders_properties(nearest[1], built_up=built_up)

    def _brussels(self, lat: float, lon: float) -> PointEvidence | None:
        streets = self._wfs(BRUSSELS_WFS, BRUSSELS_ROAD_LAYER, lat, lon, "the official Brussels UrbIS WFS")
        for feature in streets["features"]:
            if _point_in_geometry(lon, lat, feature.get("geometry")):
                properties = feature.get("properties") or {}
                return PointEvidence(
                    "City",
                    "UrbIS ADM street section",
                    f"ssft={properties.get('ssft')}; hierarchy={properties.get('hierarchy') or 'not set'}",
                )
        return None

    def _wallonia(self, lat: float, lon: float) -> PointEvidence | None:
        box = self._bbox(lat, lon)
        roads = self._request_json(
            WALLONIA_ROAD_QUERY,
            {
                "where": "1=1",
                "geometry": ",".join(f"{value:.7f}" for value in box),
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "OBJECTID,NATUR_DESC,TYPE_DESC,AMENAG",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
                "resultRecordCount": "500",
            },
            "the official Walloon PICC road-axis service",
        )
        nearest: tuple[float, dict[str, Any]] | None = None
        for feature in roads["features"]:
            properties = feature.get("properties") or {}
            nature = str(properties.get("NATUR_DESC") or "").casefold()
            if nature == "chemin ou sentier":
                continue
            distance = _line_distance_m(lat, lon, feature.get("geometry"))
            if nearest is None or distance < nearest[0]:
                nearest = (distance, properties)
        if nearest is None or nearest[0] > ROAD_SNAP_MAX_M:
            return None
        nature = str(nearest[1].get("NATUR_DESC") or "").strip()
        if nature.casefold() == "autoroute":
            return PointEvidence("Highway", "SPW PICC Voirie - Axe", f"NATUR_DESC={nature}")
        return PointEvidence(
            "Unresolved",
            "SPW PICC Voirie - Axe",
            f"NATUR_DESC={nature}; no current public built-up-road regime was found",
        )

    def __call__(self, lat: float, lon: float) -> PointEvidence:
        key = (round(lat, 7), round(lon, 7))
        with self._lock:
            cached = self._memory.get(key)
        if cached is not None:
            return cached
        errors: list[OfficialAdapterError] = []
        result: PointEvidence | None = None
        for regional_classifier in (self._flanders, self._brussels, self._wallonia):
            try:
                result = regional_classifier(lat, lon)
            except OfficialAdapterError as exc:
                errors.append(exc)
                continue
            if result is not None:
                break
        if result is None:
            if errors:
                raise errors[0]
            result = PointEvidence(
                "Unresolved",
                "Belgian regional official sources",
                "no official motor-road feature within 90 m",
            )
        with self._lock:
            self._memory[key] = result
        return result


def analyze_belgium_route(
    route: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    progress=None,
    classifier=None,
) -> dict[str, Any] | None:
    point_classifier = classifier or BelgiumPointClassifier(cache_dir)
    return analyze_sampled_route(
        route,
        country="BE",
        country_name="Belgian",
        adapter=f"Belgian regional official-data adapter {ADAPTER_VERSION}",
        classifier=point_classifier,
        official_layers={
            "Highway": "Flemish Wegenregister motorway/separated carriageway or Walloon PICC Autoroute",
            "Country": "Flemish Wegenregister outside derived built-up-area road zones",
            "City": "Flemish derived built-up-area road zones or Brussels UrbIS street sections",
            "Unresolved": "Missing/mixed evidence; Walloon non-motorway road without public built-up regime",
        },
        official_sources=[
            f"Flemish Wegenregister WFS `{FLANDERS_ROAD_LAYER}`: "
            f"{FLANDERS_ROAD_WFS}?service=WFS&request=GetCapabilities",
            f"Flemish derived built-up-road WFS `{FLANDERS_ZONE_LAYER}`: "
            f"{FLANDERS_ZONE_WFS}?service=WFS&request=GetCapabilities",
            f"Walloon PICC road-axis service and catalog: {WALLONIA_CATALOG}",
            f"Brussels UrbIS ADM WFS `{BRUSSELS_ROAD_LAYER}`: {BRUSSELS_WFS}?service=WFS&request=GetCapabilities",
        ],
        mapping_note=(
            "Regional mapping: Flemish motorway/separated morphology = Highway and official derived "
            "built-up-road coverage (with a 140 m allowance for its rounded EPSG:4326 output, after an "
            "exact Wegenregister snap) distinguishes City/Country; Brussels UrbIS street sections = City; "
            "Walloon PICC Autoroute = Highway while other Walloon road classes remain Unresolved."
        ),
        progress=progress,
    )
