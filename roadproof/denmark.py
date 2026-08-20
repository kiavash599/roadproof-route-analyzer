"""Runtime Denmark road classification against official Danish WFS data.

The Google directions preload exposes ordered maneuver anchors and exact
maneuver distances.  This adapter joins those anchors through the official
Vejdirektoratet road geometry instead of accepting a route label or a speed
limit as proof.  A maneuver is retained only when endpoint snaps and official
path length reconcile with Google's distance.  The accepted official class
lengths are then calibrated to Google's exact maneuver distance.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

from .network import NetworkRequestError, fetch_bytes, read_json_cache, write_json_cache

ADAPTER_VERSION = "dk-official-runtime-1"
WFS_ENDPOINT = "https://geocloud.vd.dk/vejman-stamdata/wfs"
WFS_SPEED_LAYER = "vejman-stamdata:hastighedsgraenser"
WFS_GUIDANCE = "https://vejman.scrollhelp.site/hjaelpecenter/anvende-stedfstelse-i-egne-programmer"
WFS_CAPABILITIES = f"{WFS_ENDPOINT}?service=WFS&request=GetCapabilities"
PLAN_WFS_ENDPOINT = "https://geoserver.plandata.dk/geoserver/wfs"
PLAN_ZONE_LAYER = "pdk:theme_pdk_zonekort_samlet_v"
PLAN_WFS_CAPABILITIES = f"{PLAN_WFS_ENDPOINT}?service=WFS&request=GetCapabilities"
PAGE_SIZE = 10_000
CACHE_MAX_AGE_S = 7 * 24 * 60 * 60
MAX_WORKERS = 4
SNAP_MAX_M = 120.0
NODE_GRID_M = 3.0
SPATIAL_CELL_M = 100.0
EARTH_RADIUS_M = 6_371_008.8
DENMARK_BOUNDS = (7.7, 54.4, 15.3, 57.9)

Progress = Callable[[str], None]
Node = tuple[int, int]
Box = tuple[float, float, float, float]


class DenmarkAdapterError(RuntimeError):
    """Raised when official Denmark data cannot be read safely."""


@dataclass(frozen=True)
class Edge:
    target: Node
    length_m: float
    category: str


@dataclass
class Graph:
    adjacency: dict[Node, list[Edge]]
    coordinates: dict[Node, tuple[float, float]]
    spatial: dict[tuple[int, int], list[Node]]


@dataclass(frozen=True)
class Zone:
    category: str
    geometry: dict[str, Any]
    bounds: Box


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))


def _metric_xy(lat: float, lon: float) -> tuple[float, float]:
    """Stable local metric coordinates suitable for indexing Denmark."""
    latitude = math.radians(lat)
    return (
        EARTH_RADIUS_M * math.radians(lon) * math.cos(latitude),
        EARTH_RADIUS_M * latitude,
    )


def _node_key(lat: float, lon: float) -> Node:
    x, y = _metric_xy(lat, lon)
    return round(x / NODE_GRID_M), round(y / NODE_GRID_M)


def _spatial_key(lat: float, lon: float) -> tuple[int, int]:
    x, y = _metric_xy(lat, lon)
    return math.floor(x / SPATIAL_CELL_M), math.floor(y / SPATIAL_CELL_M)


def classify_official_properties(properties: dict[str, Any]) -> tuple[str, str]:
    """Map documented Vejman sign/zone values to the EU road buckets.

    Road type signage has precedence.  The general-speed field is used as an
    official *inside/outside built-up-area sign regime*, not as a numeric speed
    heuristic.  Functional road class is a conservative fallback when the
    zone-sign field is absent.
    """
    signed_type = str(properties.get("VEJTYPESKILTET") or "").strip().casefold()
    signed_code = str(properties.get("KODE_VEJTYPESKILTET") or "").strip()
    zone = str(properties.get("HAST_GENEREL_HAST") or "").strip().casefold()
    zone_code = str(properties.get("KODE_HAST_GENEREL_HAST") or "").strip()
    road_class = str(properties.get("VEJSTIKLASSE") or "").strip().casefold()

    if signed_code in {"1", "2"} or signed_type in {"motorvej", "motortrafikvej"}:
        return "Highway", "VEJTYPESKILTET"
    if zone_code == "50" or "indenfor byzonetavler" in zone:
        return "City", "HAST_GENEREL_HAST"
    if zone_code == "80" or "udenfor byzonetavler" in zone:
        return "Country", "HAST_GENEREL_HAST"
    if road_class.endswith(" by") or ", by" in road_class:
        return "City", "VEJSTIKLASSE"
    if road_class.endswith(" land") or ", land" in road_class:
        return "Country", "VEJSTIKLASSE"
    return "Unresolved", "insufficient official attributes"


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache" / "denmark-wfs"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "roadproof" / "denmark-wfs"


def _cache_path(params: dict[str, str], cache_dir: Path | None) -> Path:
    root = cache_dir or _cache_root()
    key = hashlib.sha256(urlencode(sorted(params.items())).encode("utf-8")).hexdigest()
    return root / f"{ADAPTER_VERSION}-{key}.json"


def _read_cache(path: Path) -> dict[str, Any] | None:
    value = read_json_cache(path, max_age_s=CACHE_MAX_AGE_S)
    return value if value is not None and isinstance(value.get("features"), list) else None


def _write_cache(path: Path, value: dict[str, Any]) -> None:
    write_json_cache(path, value)


def _request_json_page(
    endpoint: str,
    params: dict[str, str],
    *,
    service_name: str,
    timeout: int = 90,
) -> dict[str, Any]:
    url = f"{endpoint}?{urlencode(params)}"
    try:
        body = fetch_bytes(
            url,
            headers={
                "User-Agent": "RoadProof/0.6 (+https://github.com/kiavash599/roadproof-route-analyzer)",
                "Accept": "application/json",
            },
            timeout=timeout,
            max_bytes=64 * 1024 * 1024,
            validate_final_url=lambda final: final.split("?", 1)[0] == endpoint,
        )
        payload = json.loads(body)
    except (NetworkRequestError, json.JSONDecodeError) as exc:
        raise DenmarkAdapterError(f"Could not read {service_name}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise DenmarkAdapterError(f"{service_name} returned an unrecognized response.")
    return payload


def _request_page(params: dict[str, str], *, timeout: int = 90) -> dict[str, Any]:
    return _request_json_page(
        WFS_ENDPOINT,
        params,
        service_name="the official Vejman WFS",
        timeout=timeout,
    )


def _fetch_layer_box(
    box: Box,
    cache_dir: Path | None,
    *,
    layer: str,
    properties: str,
) -> dict[str, Any]:
    common = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": ",".join(f"{value:.6f}" for value in box) + ",EPSG:4326",
        "propertyName": properties,
        "count": str(PAGE_SIZE),
    }
    cache_path = _cache_path(common, cache_dir)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    first = _request_page(common)
    features = list(first["features"])
    matched = int(first.get("numberMatched") or first.get("totalFeatures") or len(features))
    if len(features) < matched:
        # This GeoServer layer has no primary key, so WFS startIndex paging is
        # rejected. Split a busy corridor spatially and deduplicate instead.
        west, south, east, north = box
        if max(east - west, north - south) < 0.0002:
            raise DenmarkAdapterError(
                "The official Vejman WFS result exceeded its limit in a minimum-size corridor."
            )
        if east - west >= north - south:
            middle = (west + east) / 2
            children = ((west, south, middle, north), (middle, south, east, north))
        else:
            middle = (south + north) / 2
            children = ((west, south, east, middle), (west, middle, east, north))
        child_features: dict[str, dict[str, Any]] = {}
        child_timestamps: list[str] = []
        for child in children:
            collection = _fetch_layer_box(
                child,
                cache_dir,
                layer=layer,
                properties=properties,
            )
            if collection.get("timeStamp"):
                child_timestamps.append(str(collection["timeStamp"]))
            for feature in collection["features"]:
                child_features[str(feature.get("id"))] = feature
        combined = {
            "type": "FeatureCollection",
            "features": list(child_features.values()),
            "numberMatched": len(child_features),
            "timeStamp": max(child_timestamps) if child_timestamps else first.get("timeStamp"),
        }
        _write_cache(cache_path, combined)
        return combined
    combined = {
        "type": "FeatureCollection",
        "features": features,
        "numberMatched": matched,
        "timeStamp": first.get("timeStamp"),
    }
    _write_cache(cache_path, combined)
    return combined


def _fetch_box(box: Box, cache_dir: Path | None = None) -> dict[str, Any]:
    return _fetch_layer_box(
        box,
        cache_dir,
        layer=WFS_SPEED_LAYER,
        properties=(
            "BESTYRER,ADMVEJNR,CPR_VEJNAVN,HAST_GENEREL_HAST,KODE_HAST_GENEREL_HAST,"
            "HAST_BYKODE,KODE_HAST_BYKODE,VEJSTIKLASSE,KODE_VEJSTIKLASSE,"
            "VEJTYPESKILTET,KODE_VEJTYPESKILTET,geometry"
        ),
    )


def _fetch_zone_box(box: Box, cache_dir: Path | None = None) -> dict[str, Any]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": PLAN_ZONE_LAYER,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": ",".join(f"{value:.6f}" for value in box) + ",EPSG:4326",
        "propertyName": "id,komnr,kommunenavn,zone,zonestatus,datoopdt,geometri",
        "count": "1000",
    }
    cache_path = _cache_path(params, cache_dir)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached
    first = _request_json_page(
        PLAN_WFS_ENDPOINT,
        params,
        service_name="the official Plandata zone WFS",
    )
    features = list(first["features"])
    matched = int(first.get("numberMatched") or first.get("totalFeatures") or len(features))
    if len(features) < matched:
        west, south, east, north = box
        if max(east - west, north - south) < 0.0002:
            raise DenmarkAdapterError(
                "The official Plandata WFS result exceeded its limit in a minimum-size corridor."
            )
        if east - west >= north - south:
            middle = (west + east) / 2
            children = ((west, south, middle, north), (middle, south, east, north))
        else:
            middle = (south + north) / 2
            children = ((west, south, east, middle), (west, middle, east, north))
        child_features: dict[str, dict[str, Any]] = {}
        child_timestamps: list[str] = []
        for child in children:
            collection = _fetch_zone_box(child, cache_dir)
            if collection.get("timeStamp"):
                child_timestamps.append(str(collection["timeStamp"]))
            for feature in collection["features"]:
                child_features[str(feature.get("id"))] = feature
        combined = {
            "type": "FeatureCollection",
            "features": list(child_features.values()),
            "numberMatched": len(child_features),
            "timeStamp": max(child_timestamps) if child_timestamps else first.get("timeStamp"),
        }
        _write_cache(cache_path, combined)
        return combined
    combined = {
        "type": "FeatureCollection",
        "features": features,
        "numberMatched": matched,
        "timeStamp": first.get("timeStamp"),
    }
    _write_cache(cache_path, combined)
    return combined


def _box_for_maneuver(maneuver: dict[str, Any]) -> Box:
    start = (float(maneuver["start_lat"]), float(maneuver["start_lon"]))
    end = (float(maneuver["end_lat"]), float(maneuver["end_lon"]))
    distance_m = max(0.0, float(maneuver["distance_m"]))
    chord_m = _haversine(start, end)
    detour_m = max(0.0, distance_m - chord_m)
    padding_m = max(500.0, min(3_000.0, 0.08 * distance_m + 0.35 * detour_m))
    mean_lat = (start[0] + end[0]) / 2
    latitude_padding = padding_m / 111_320.0
    longitude_padding = latitude_padding / max(0.2, math.cos(math.radians(mean_lat)))
    return (
        min(start[1], end[1]) - longitude_padding,
        min(start[0], end[0]) - latitude_padding,
        max(start[1], end[1]) + longitude_padding,
        max(start[0], end[0]) + latitude_padding,
    )


def _box_area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _boxes_overlap(a: Box, b: Box) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _merge_boxes(boxes: Iterable[Box]) -> list[Box]:
    """Merge strongly overlapping requests without turning a route into one huge bbox."""
    merged: list[Box] = []
    for box in sorted(boxes):
        for index, existing in enumerate(merged):
            if not _boxes_overlap(box, existing):
                continue
            union = (
                min(box[0], existing[0]),
                min(box[1], existing[1]),
                max(box[2], existing[2]),
                max(box[3], existing[3]),
            )
            if (
                _box_area(union) <= 1.8 * (_box_area(box) + _box_area(existing))
                and _box_area(union) <= 0.025
                and union[2] - union[0] <= 0.35
                and union[3] - union[1] <= 0.25
            ):
                merged[index] = union
                break
        else:
            merged.append(box)
    return merged


def _iter_lines(geometry: dict[str, Any] | None) -> Iterable[list[list[float]]]:
    if not geometry:
        return
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "LineString" and isinstance(coordinates, list):
        yield coordinates
    elif geometry_type == "MultiLineString" and isinstance(coordinates, list):
        for line in coordinates:
            if isinstance(line, list):
                yield line


def _iter_polygon_rings(geometry: dict[str, Any]) -> Iterable[list[list[list[float]]]]:
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        yield coordinates
    elif geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        for polygon in coordinates:
            if isinstance(polygon, list):
                yield polygon


def _geometry_bounds(geometry: dict[str, Any]) -> Box | None:
    west = south = math.inf
    east = north = -math.inf
    found = False
    for polygon in _iter_polygon_rings(geometry):
        for ring in polygon:
            for coordinate in ring:
                if not isinstance(coordinate, list) or len(coordinate) < 2:
                    continue
                lon, lat = float(coordinate[0]), float(coordinate[1])
                west, south = min(west, lon), min(south, lat)
                east, north = max(east, lon), max(north, lat)
                found = True
    return (west, south, east, north) if found else None


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > lat) != (y2 > lat):
            intersection = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < intersection:
                inside = not inside
        previous = current
    return inside


def _point_in_zone(lon: float, lat: float, zone: Zone) -> bool:
    if not (zone.bounds[0] <= lon <= zone.bounds[2] and zone.bounds[1] <= lat <= zone.bounds[3]):
        return False
    for polygon in _iter_polygon_rings(zone.geometry):
        if not polygon or not _point_in_ring(lon, lat, polygon[0]):
            continue
        if any(_point_in_ring(lon, lat, hole) for hole in polygon[1:]):
            continue
        return True
    return False


def _prepare_zones(features: Iterable[dict[str, Any]]) -> list[Zone]:
    zones: list[Zone] = []
    for feature in features:
        properties = feature.get("properties") or {}
        code = str(properties.get("zone") or "").strip()
        status = str(properties.get("zonestatus") or "").strip().casefold()
        category = "City" if code == "1" or status == "byzone" else "Country" if code in {"2", "3"} or status in {"landzone", "sommerhusområde"} else ""
        geometry = feature.get("geometry")
        bounds = _geometry_bounds(geometry) if isinstance(geometry, dict) else None
        if category and bounds is not None:
            zones.append(Zone(category, geometry, bounds))
    return zones


def _zone_category(lon: float, lat: float, zones: Iterable[Zone]) -> str:
    matches = {zone.category for zone in zones if _point_in_zone(lon, lat, zone)}
    if "City" in matches:
        return "City"
    if "Country" in matches:
        return "Country"
    return "Unresolved"


def _category_rank(category: str) -> int:
    return {"Highway": 4, "City": 3, "Country": 3, "Unresolved": 1}.get(category, 0)


def build_graph(
    features: Iterable[dict[str, Any]],
    zone_features: Iterable[dict[str, Any]] = (),
) -> Graph:
    coordinates: dict[Node, tuple[float, float]] = {}
    best_edges: dict[tuple[Node, Node], tuple[float, str]] = {}
    zones = _prepare_zones(zone_features)
    for feature in features:
        properties = feature.get("properties") or {}
        category, _source = classify_official_properties(properties)
        for line in _iter_lines(feature.get("geometry")):
            previous: tuple[Node, tuple[float, float]] | None = None
            for coordinate in line:
                if not isinstance(coordinate, list) or len(coordinate) < 2:
                    previous = None
                    continue
                lon, lat = float(coordinate[0]), float(coordinate[1])
                node = _node_key(lat, lon)
                coordinates.setdefault(node, (lat, lon))
                current = (node, (lat, lon))
                if previous is not None and previous[0] != node:
                    length_m = _haversine(previous[1], current[1])
                    if 0.05 <= length_m <= 2_000:
                        edge_category = category
                        if edge_category == "Unresolved" and zones:
                            midpoint_lon = (previous[1][1] + current[1][1]) / 2
                            midpoint_lat = (previous[1][0] + current[1][0]) / 2
                            edge_category = _zone_category(midpoint_lon, midpoint_lat, zones)
                        key = tuple(sorted((previous[0], node)))
                        existing = best_edges.get(key)
                        if existing is None or _category_rank(edge_category) > _category_rank(existing[1]):
                            best_edges[key] = (length_m, edge_category)
                previous = current

    adjacency: dict[Node, list[Edge]] = {node: [] for node in coordinates}
    for (first, second), (length_m, category) in best_edges.items():
        adjacency[first].append(Edge(second, length_m, category))
        adjacency[second].append(Edge(first, length_m, category))
    spatial: dict[tuple[int, int], list[Node]] = {}
    for node, (lat, lon) in coordinates.items():
        spatial.setdefault(_spatial_key(lat, lon), []).append(node)
    return Graph(adjacency, coordinates, spatial)


def _nearest_nodes(graph: Graph, point: tuple[float, float], *, limit: int = 5) -> list[tuple[float, Node]]:
    base = _spatial_key(*point)
    candidates: set[Node] = set()
    radius = math.ceil(SNAP_MAX_M / SPATIAL_CELL_M) + 1
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            candidates.update(graph.spatial.get((base[0] + dx, base[1] + dy), []))
    ranked = sorted(
        (_haversine(point, graph.coordinates[node]), node)
        for node in candidates
    )
    return [item for item in ranked[:limit] if item[0] <= SNAP_MAX_M]


def _reconstruct_path(
    came_from: dict[Node, tuple[Node, Edge]],
    end: Node,
) -> list[Edge]:
    edges: list[Edge] = []
    current = end
    while current in came_from:
        previous, edge = came_from[current]
        edges.append(edge)
        current = previous
    edges.reverse()
    return edges


def _a_star(graph: Graph, start: Node, goal: Node, max_distance_m: float) -> list[Edge] | None:
    if start == goal:
        return []
    goal_coordinate = graph.coordinates[goal]
    queue: list[tuple[float, float, float, Node]] = [(0.0, 0.0, 0.0, start)]
    best: dict[Node, float] = {start: 0.0}
    came_from: dict[Node, tuple[Node, Edge]] = {}
    while queue:
        _estimated, cost, distance, node = heapq.heappop(queue)
        if cost != best.get(node):
            continue
        if node == goal:
            return _reconstruct_path(came_from, goal)
        if distance > max_distance_m:
            continue
        for edge in graph.adjacency.get(node, []):
            candidate_distance = distance + edge.length_m
            evidence_penalty = 1.08 if edge.category == "Unresolved" else 1.0
            candidate_cost = cost + edge.length_m * evidence_penalty
            if candidate_distance > max_distance_m or candidate_cost >= best.get(edge.target, math.inf):
                continue
            best[edge.target] = candidate_cost
            came_from[edge.target] = (node, edge)
            heuristic = _haversine(graph.coordinates[edge.target], goal_coordinate)
            heapq.heappush(
                queue,
                (candidate_cost + heuristic, candidate_cost, candidate_distance, edge.target),
            )
    return None


def _match_maneuver(graph: Graph, maneuver: dict[str, Any]) -> dict[str, Any]:
    google_distance = float(maneuver["distance_m"])
    start_point = (float(maneuver["start_lat"]), float(maneuver["start_lon"]))
    end_point = (float(maneuver["end_lat"]), float(maneuver["end_lon"]))
    starts = _nearest_nodes(graph, start_point)
    ends = _nearest_nodes(graph, end_point)
    if not starts or not ends:
        return {"matched": False, "reason": "official endpoint snap exceeded 120 m"}

    best_match: tuple[float, float, float, list[Edge]] | None = None
    max_path = max(1_000.0, google_distance * 1.45 + 500.0)
    for start_snap, start in starts:
        for end_snap, end in ends:
            path = _a_star(graph, start, end, max_path)
            if path is None:
                continue
            path_distance = sum(edge.length_m for edge in path) + start_snap + end_snap
            score = abs(path_distance - google_distance) + 0.25 * (start_snap + end_snap)
            if best_match is None or score < best_match[0]:
                best_match = (score, start_snap, end_snap, path)
    if best_match is None:
        return {"matched": False, "reason": "no official connected path between maneuver anchors"}

    _score, start_snap, end_snap, path = best_match
    official_distance = sum(edge.length_m for edge in path) + start_snap + end_snap
    difference = abs(official_distance - google_distance)
    tolerance = max(100.0, google_distance * 0.06)
    if difference > tolerance:
        return {
            "matched": False,
            "reason": f"official path differed by {difference:.0f} m ({100*difference/max(1.0,google_distance):.1f}%)",
            "start_snap_m": start_snap,
            "end_snap_m": end_snap,
            "official_distance_m": official_distance,
        }

    category_lengths = {"Highway": 0.0, "Country": 0.0, "City": 0.0, "Unresolved": 0.0}
    edge_total = sum(edge.length_m for edge in path)
    if edge_total <= 0:
        category_lengths["Unresolved"] = google_distance
    else:
        for edge in path:
            category_lengths[edge.category] += google_distance * edge.length_m / edge_total
    return {
        "matched": True,
        "categories": category_lengths,
        "start_snap_m": start_snap,
        "end_snap_m": end_snap,
        "official_distance_m": official_distance,
        "difference_m": difference,
    }


def _inside_denmark_bounds(route: dict[str, Any]) -> bool:
    west, south, east, north = DENMARK_BOUNDS
    return all(
        south <= float(item["start_lat"]) <= north
        and west <= float(item["start_lon"]) <= east
        and south <= float(item["end_lat"]) <= north
        and west <= float(item["end_lon"]) <= east
        for item in route.get("maneuvers", [])
    )


def analyze_denmark_route(
    route: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    progress: Progress | None = None,
    fetch_box: Callable[[Box, Path | None], dict[str, Any]] = _fetch_box,
    fetch_zones: Callable[[Box, Path | None], dict[str, Any]] = _fetch_zone_box,
) -> dict[str, Any] | None:
    """Return a runtime official evidence package for an all-Denmark route."""
    started = time.monotonic()
    countries = {str(value).upper() for value in route.get("countries", []) if value}
    if countries and countries != {"DK"}:
        return None
    if not route.get("maneuvers") or not _inside_denmark_bounds(route):
        return None

    boxes = _merge_boxes(_box_for_maneuver(item) for item in route["maneuvers"])
    route_box = (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    if progress:
        progress(f"Querying official Danish road and zone data for {len(boxes)} route corridor(s)...")
    collections: list[dict[str, Any]] = []
    zone_collection: dict[str, Any] | None = None
    zone_error: str | None = None
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures: dict[Any, str] = {
            executor.submit(fetch_box, box, cache_dir): "roads" for box in boxes
        }
        futures[executor.submit(fetch_zones, route_box, cache_dir)] = "zones"
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except DenmarkAdapterError as exc:
                if source == "zones":
                    zone_error = str(exc)
                    continue
                raise
            if source == "zones":
                zone_collection = result
            else:
                collections.append(result)

    features_by_id: dict[str, dict[str, Any]] = {}
    timestamps: list[str] = []
    for collection in collections:
        if collection.get("timeStamp"):
            timestamps.append(str(collection["timeStamp"]))
        for feature in collection["features"]:
            identifier = str(feature.get("id") or hashlib.sha256(json.dumps(feature, sort_keys=True).encode()).hexdigest())
            features_by_id[identifier] = feature
    if zone_collection is None:
        zone_collection = {"type": "FeatureCollection", "features": []}
    if zone_collection.get("timeStamp"):
        timestamps.append(str(zone_collection["timeStamp"]))
    if not features_by_id:
        raise DenmarkAdapterError("The official Vejman query returned no road geometry near this route.")
    if progress:
        progress(
            f"Building the official road graph from {len(features_by_id):,} road feature(s) "
            f"and {len(zone_collection['features']):,} zone feature(s)..."
        )
    graph = build_graph(features_by_id.values(), zone_collection["features"])

    totals = {"Highway": 0.0, "Country": 0.0, "City": 0.0, "Unresolved": 0.0}
    diagnostics: list[dict[str, Any]] = []
    matched_count = 0
    for maneuver in route["maneuvers"]:
        result = _match_maneuver(graph, maneuver)
        diagnostic = {
            "sequence": int(maneuver["sequence"]),
            "google_distance_m": float(maneuver["distance_m"]),
            **result,
        }
        diagnostics.append(diagnostic)
        if result["matched"]:
            matched_count += 1
            for category, distance in result["categories"].items():
                totals[category] += float(distance)
        else:
            totals["Unresolved"] += float(maneuver["distance_m"])

    route_total = float(route["distance_m"])
    allocated = sum(totals.values())
    if route_total < 0 or not math.isfinite(route_total):
        raise DenmarkAdapterError("The Google route total was not a finite non-negative distance.")
    if allocated > 0:
        scale = route_total / allocated
        totals = {category: max(0.0, distance * scale) for category, distance in totals.items()}
    else:
        totals["Unresolved"] = route_total
    matched_distance = sum(totals[category] for category in ("Highway", "Country", "City"))
    if matched_distance <= 0:
        raise DenmarkAdapterError("No maneuver passed the official Denmark map-matching gates.")

    rows = [
        {
            "category": category,
            "official_layer": (
                "Vejman signed motorway / expressway" if category == "Highway"
                else "Vejman built-up regime / official Plandata Byzone fallback" if category == "City"
                else "Vejman non-urban regime / official Plandata Landzone fallback" if category == "Country"
                else "Maneuvers that failed official snap/path reconciliation or lacked decisive attributes"
            ),
            "distance_m": totals[category],
            "status": (
                "Official source matched; EU bucket and Google distance allocation inferred"
                if category != "Unresolved" and totals[category] > 0.01
                else "No matched segments"
                if category != "Unresolved"
                else "Unresolved"
            ),
        }
        for category in ("Highway", "Country", "City", "Unresolved")
    ]
    unresolved_details = [item for item in diagnostics if not item["matched"]]
    retrieval_time = max(timestamps) if timestamps else datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": 2,
        "name": "Runtime Denmark official-road analysis",
        "analysis_date": datetime.now(timezone.utc).date().isoformat(),
        "country": "DK",
        "adapter": f"Danish Vejman + Plandata adapter {ADAPTER_VERSION}",
        "runtime": True,
        "summary": "Runtime match against official Danish road and zone data",
        "route_identity": {
            "route_fingerprint": route["route_fingerprint"],
            "distance_m": route["distance_m"],
            "maneuver_count": route["maneuver_count"],
        },
        "breakdown": rows,
        "matched_maneuvers": matched_count,
        "matched_distance_m": matched_distance,
        "performance": {
            "analysis_seconds": round(time.monotonic() - started, 3),
            "corridor_count": len(boxes),
            "road_feature_count": len(features_by_id),
            "zone_feature_count": len(zone_collection["features"]),
            "graph_node_count": len(graph.coordinates),
            "maneuver_count": len(route["maneuvers"]),
        },
        "diagnostics": diagnostics,
        "inferred": [
            "Google maneuver distances were allocated in proportion to the accepted official Vejman path classes.",
            f"{matched_count} of {route['maneuver_count']} maneuvers passed the 120 m endpoint-snap and max(100 m, 6%) path-distance gates.",
            "Operational mapping: Highway = officially signed Motorvej/Motortrafikvej; City/Country = official inside/outside built-up-area sign regime, then Vejstiklasse, then the official Plandata Byzone/Landzone map when Vejman attributes are absent.",
        ],
        "unresolved": [
            f"{totals['Unresolved'] / 1000:.3f} km remained unresolved across {len(unresolved_details)} maneuver(s).",
            "Dual-carriageway status without decisive official sign/zone attributes is not guessed.",
            *([f"Plandata zone fallback was unavailable: {zone_error}"] if zone_error else []),
            *(f"Maneuver {item['sequence'] + 1}: {item['reason']}." for item in unresolved_details[:12]),
        ],
        "method": (
            "RoadProof queried the public Vejdirektoratet Vejman WFS around the Google maneuver corridors, "
            "used the official Plandata zone map only where Vejman road attributes were absent, built an "
            "official road graph, snapped each maneuver endpoint within 120 m, and accepted only "
            "connected paths whose length differed from Google's maneuver distance by no more than max(100 m, 6%). "
            "Official path classes were then calibrated to the exact Google distance; failed paths were not redistributed."
        ),
        "official_sources": [
            f"Vejman WFS layer `{WFS_SPEED_LAYER}`; response timestamp {retrieval_time}.",
            f"Vejman WFS capabilities: {WFS_CAPABILITIES}",
            f"Vejdirektoratet guidance for using Vejman data in external GIS: {WFS_GUIDANCE}",
            f"Plandata WFS layer `{PLAN_ZONE_LAYER}`: {PLAN_WFS_CAPABILITIES}",
        ],
    }
