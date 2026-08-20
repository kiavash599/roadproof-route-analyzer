"""Exact-track import and identity, kept separate from Google route evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from xml.etree import ElementTree


MAX_TRACK_BYTES = 5_000_000
GEOMETRY_HASH_VERSION = "roadproof-track-geometry-v1"
EARTH_RADIUS_M = 6_371_008.8


class TrackReadError(RuntimeError):
    """The supplied exact-track file was unsafe or unrecognized."""


def _point(lat_value: Any, lon_value: Any) -> tuple[float, float]:
    try:
        lat, lon = float(lat_value), float(lon_value)
    except (TypeError, ValueError) as exc:
        raise TrackReadError("The track contains an invalid latitude or longitude.") from exc
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise TrackReadError("The track contains an invalid latitude or longitude.")
    return lat, lon


def _geojson_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, dict):
        return []
    kind = value.get("type")
    if kind == "Feature":
        return _geojson_points(value.get("geometry"))
    if kind == "FeatureCollection":
        return [point for feature in value.get("features", []) for point in _geojson_points(feature)]
    if kind == "GeometryCollection":
        return [point for geometry in value.get("geometries", []) for point in _geojson_points(geometry)]
    if kind == "LineString":
        points = []
        for coordinate in value.get("coordinates", []):
            if not isinstance(coordinate, list) or len(coordinate) < 2:
                raise TrackReadError("A GeoJSON coordinate is incomplete.")
            points.append(_point(coordinate[1], coordinate[0]))
        return points
    if kind == "MultiLineString":
        return [
            point
            for line in value.get("coordinates", [])
            for point in _geojson_points({"type": "LineString", "coordinates": line})
        ]
    return []


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()


def _gpx_points(root: ElementTree.Element) -> list[tuple[float, float]]:
    points = []
    for element in root.iter():
        if _xml_local_name(element.tag) not in {"trkpt", "rtept"}:
            continue
        if "lat" not in element.attrib or "lon" not in element.attrib:
            raise TrackReadError("A GPX track point is missing latitude or longitude.")
        points.append(_point(element.attrib["lat"], element.attrib["lon"]))
    return points


def _kml_points(root: ElementTree.Element) -> list[tuple[float, float]]:
    points = []
    for element in root.iter():
        if _xml_local_name(element.tag) != "coordinates" or not element.text:
            continue
        for item in element.text.strip().split():
            coordinate = item.split(",")
            if len(coordinate) < 2:
                raise TrackReadError("A KML coordinate is incomplete.")
            points.append(_point(coordinate[1], coordinate[0]))
    return points


def _distance_m(points: list[tuple[float, float]]) -> float:
    distance = 0.0
    for previous, current in zip(points, points[1:]):
        lat1, lon1 = map(math.radians, previous)
        lat2, lon2 = map(math.radians, current)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        distance += EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))
    return distance


def track_distance_m(points: list[tuple[float, float]]) -> float:
    """Return ordered geodesic track length in metres."""
    return _distance_m(points)


def track_from_points(points: list[tuple[float, float]], *, track_format: str, source_name: str) -> dict[str, Any]:
    if len(points) < 2:
        raise TrackReadError("The track must contain at least two ordered points.")
    normalized = [_point(lat, lon) for lat, lon in points]
    canonical = "\n".join(f"{lat:.7f},{lon:.7f}" for lat, lon in normalized)
    return {
        "format": track_format,
        "point_count": len(normalized),
        "distance_m": _distance_m(normalized),
        "geometry_hash": hashlib.sha256(f"{GEOMETRY_HASH_VERSION}\n{canonical}".encode()).hexdigest(),
        "geometry_hash_version": GEOMETRY_HASH_VERSION,
        "start": normalized[0],
        "end": normalized[-1],
        "points": normalized,
        "source_name": source_name,
    }


def read_track(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TrackReadError(f"Could not read the exact-track file: {exc}") from exc
    if size > MAX_TRACK_BYTES:
        raise TrackReadError("Track files are limited to 5 MB.")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise TrackReadError(f"Could not read the exact-track file as UTF-8: {exc}") from exc

    suffix = path.suffix.casefold()
    try:
        if suffix == ".gpx" or re.search(r"<\s*gpx[\s>]", text, re.IGNORECASE):
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
                raise TrackReadError("Track XML declarations and entities are not permitted.")
            track_format = "GPX"
            points = _gpx_points(ElementTree.fromstring(text))
        elif suffix == ".kml" or re.search(r"<\s*kml[\s>]", text, re.IGNORECASE):
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
                raise TrackReadError("Track XML declarations and entities are not permitted.")
            track_format = "KML"
            points = _kml_points(ElementTree.fromstring(text))
        else:
            track_format = "GeoJSON"
            points = _geojson_points(json.loads(text))
    except (ElementTree.ParseError, json.JSONDecodeError) as exc:
        raise TrackReadError("The file is not valid GPX, KML, or GeoJSON.") from exc
    if len(points) < 2:
        raise TrackReadError("The file must contain at least two ordered track points.")
    return track_from_points(points, track_format=track_format, source_name=path.name)


def _haversine(first: tuple[float, float], second: tuple[float, float]) -> float:
    return _distance_m([first, second])


def reconcile_track(route: dict[str, Any], track: dict[str, Any]) -> dict[str, Any]:
    maneuvers = route.get("maneuvers") or []
    if not maneuvers:
        raise TrackReadError("The Google route contains no maneuver endpoints for track reconciliation.")
    route_start = (float(maneuvers[0]["start_lat"]), float(maneuvers[0]["start_lon"]))
    route_end = (float(maneuvers[-1]["end_lat"]), float(maneuvers[-1]["end_lon"]))
    direct = (_haversine(track["start"], route_start), _haversine(track["end"], route_end))
    reverse = (_haversine(track["end"], route_start), _haversine(track["start"], route_end))
    orientation = "forward" if sum(direct) <= sum(reverse) else "reverse"
    endpoint_distances = direct if orientation == "forward" else reverse
    distance_difference = abs(float(track["distance_m"]) - float(route["distance_m"]))
    distance_tolerance = max(100.0, float(route["distance_m"]) * 0.005)
    endpoints_consistent = max(endpoint_distances) <= 120.0
    distance_consistent = distance_difference <= distance_tolerance
    status = "consistent_not_equivalent" if endpoints_consistent and distance_consistent else "conflict"
    return {
        "status": status,
        "equivalence_claimed": False,
        "orientation": orientation,
        "start_difference_m": endpoint_distances[0],
        "end_difference_m": endpoint_distances[1],
        "distance_difference_m": distance_difference,
        "distance_tolerance_m": distance_tolerance,
        "note": (
            "Track length and endpoints are consistent with the Google maneuver evidence; exact equivalence is not claimed."
            if status == "consistent_not_equivalent"
            else "The imported track conflicts with the Google maneuver distance or endpoints; it was not used for classification."
        ),
    }
