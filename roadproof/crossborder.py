"""Conservative cross-border dispatch from a reconciled imported exact track."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .adapters import COUNTRY_ENVELOPES, SUPPORTED_COUNTRIES, analyze_supported_route
from .denmark import DenmarkAdapterError
from .network import NetworkRequestError, fetch_bytes, read_json_cache, write_json_cache
from .sampled import OfficialAdapterError
from .track import track_distance_m


CROSS_BORDER_VERSION = "cross-border-track-v2"
MAX_TRACK_EDGES = 1_000
BOUNDARY_YEAR = 2024
BOUNDARY_CACHE_MAX_AGE_S = 30 * 24 * 60 * 60
BOUNDARY_SAMPLE_MAX_M = 2_000.0
GISCO_BASE = "https://gisco-services.ec.europa.eu/distribution/v2/countries/distribution"
Analyzer = Callable[..., dict[str, Any] | None]
BoundaryContains = Callable[[str, tuple[float, float]], bool]


def _inside(point: tuple[float, float], envelope: tuple[float, float, float, float]) -> bool:
    lat, lon = point
    south, north, west, east = envelope
    return south <= lat <= north and west <= lon <= east


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RoadProof" / "cache" / "country-boundaries"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "roadproof" / "country-boundaries"


def _boundary_url(country: str) -> str:
    return f"{GISCO_BASE}/{country}-region-01m-4326-{BOUNDARY_YEAR}.geojson"


def _valid_boundary_redirect(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "gisco-services.ec.europa.eu" and parsed.path.startswith(
        "/distribution/v2/countries/distribution/"
    )


def _load_boundary(country: str, cache_dir: Path | None = None) -> dict[str, Any]:
    path = (cache_dir or _cache_root()) / f"{country}-01m-4326-{BOUNDARY_YEAR}.json"
    cached = read_json_cache(path, max_age_s=BOUNDARY_CACHE_MAX_AGE_S)
    if cached is not None:
        return cached
    try:
        value = json.loads(
            fetch_bytes(
                _boundary_url(country),
                headers={"User-Agent": "RoadProof/0.7"},
                max_bytes=16 * 1024 * 1024,
                validate_final_url=_valid_boundary_redirect,
            ).decode("utf-8")
        )
    except (NetworkRequestError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialAdapterError(f"Official GISCO boundary for {country} was unavailable: {exc}") from exc
    if not isinstance(value, dict) or value.get("type") not in {"Feature", "FeatureCollection"}:
        raise OfficialAdapterError(f"Official GISCO boundary for {country} had an unexpected GeoJSON structure.")
    write_json_cache(path, value)
    return value


def _point_in_ring(lon: float, lat: float, ring: list[Any]) -> bool:
    inside = False
    previous = ring[-1] if ring else None
    for current in ring:
        if not (
            isinstance(previous, list) and len(previous) >= 2 and isinstance(current, list) and len(current) >= 2
        ):
            previous = current
            continue
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def _point_in_polygon(point: tuple[float, float], polygon: list[Any]) -> bool:
    lat, lon = point
    return bool(polygon) and _point_in_ring(lon, lat, polygon[0]) and not any(
        _point_in_ring(lon, lat, hole) for hole in polygon[1:]
    )


def _geometry_contains(geometry: dict[str, Any], point: tuple[float, float]) -> bool:
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        return _point_in_polygon(point, coordinates)
    if geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        return any(_point_in_polygon(point, polygon) for polygon in coordinates)
    return False


def _geojson_contains(value: dict[str, Any], point: tuple[float, float]) -> bool:
    if value.get("type") == "FeatureCollection":
        return any(
            isinstance(feature, dict) and _geometry_contains(feature.get("geometry") or {}, point)
            for feature in value.get("features", [])
        )
    return _geometry_contains(value.get("geometry") or {}, point)


def _point_country(
    point: tuple[float, float], allowed: set[str], contains: BoundaryContains
) -> tuple[str | None, list[str]]:
    candidates = sorted(
        country
        for country in allowed
        if country in COUNTRY_ENVELOPES
        and _inside(point, COUNTRY_ENVELOPES[country])
        and contains(country, point)
    )
    return (candidates[0] if len(candidates) == 1 else None), candidates


def _edge_samples(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
    count = max(1, math.ceil(track_distance_m([start, end]) / BOUNDARY_SAMPLE_MAX_M))
    return [
        (start[0] + (end[0] - start[0]) * index / count, start[1] + (end[1] - start[1]) * index / count)
        for index in range(count + 1)
    ]


def _subroute(
    route: dict[str, Any],
    country: str,
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    distance_m = sum(float(edge["distance_m"]) for edge in edges)
    fingerprint_body = json.dumps(
        {
            "version": CROSS_BORDER_VERSION,
            "geometry_hash": route["geometry_hash"],
            "country": country,
            "edges": [edge["sequence"] for edge in edges],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "countries": [country],
        "distance_m": distance_m,
        "route_fingerprint": hashlib.sha256(fingerprint_body).hexdigest(),
        "route_fingerprint_version": CROSS_BORDER_VERSION,
        "maneuver_count": len(edges),
        "maneuvers": edges,
    }


def analyze_cross_border_track(
    route: dict[str, Any],
    *,
    progress=None,
    analyzer: Analyzer = analyze_supported_route,
    boundary_contains: BoundaryContains | None = None,
    boundary_cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    countries = {str(value).upper() for value in route.get("countries", []) if value}
    points = route.get("_exact_track_points")
    reconciliation = route.get("track_reconciliation") or {}
    if len(countries) < 2 or not points or reconciliation.get("status") != "consistent_not_equivalent":
        return None
    if len(points) - 1 > MAX_TRACK_EDGES:
        raise OfficialAdapterError(
            f"The exact track has {len(points) - 1:,} edges; simplify it to at most {MAX_TRACK_EDGES:,} edges."
        )

    allowed = countries & set(SUPPORTED_COUNTRIES)
    if boundary_contains is None:
        boundaries = {country: _load_boundary(country, boundary_cache_dir) for country in sorted(allowed)}

        def boundary_contains(country: str, point: tuple[float, float]) -> bool:
            return _geojson_contains(boundaries[country], point)

    by_country: dict[str, list[dict[str, Any]]] = {country: [] for country in sorted(allowed)}
    unresolved_distance = 0.0
    edge_diagnostics: list[dict[str, Any]] = []
    for sequence, (start, end) in enumerate(zip(points, points[1:])):
        distance_m = track_distance_m([start, end])
        sample_results = [_point_country(point, allowed, boundary_contains) for point in _edge_samples(start, end)]
        start_country, start_candidates = sample_results[0]
        end_country, end_candidates = sample_results[-1]
        sample_countries = {country for country, _candidates in sample_results}
        country = next(iter(sample_countries)) if len(sample_countries) == 1 and None not in sample_countries else None
        if country is None:
            unresolved_distance += distance_m
            reason = (
                "country transition or official-boundary ambiguity"
                if start_candidates or end_candidates
                else "unsupported or outside supported country guards"
            )
        else:
            reason = "all sampled exact-track edge points passed one unambiguous official-country boundary guard"
            by_country[country].append(
                {
                    "sequence": sequence,
                    "distance_m": distance_m,
                    "start_lat": start[0],
                    "start_lon": start[1],
                    "end_lat": end[0],
                    "end_lon": end[1],
                    "instruction": "Imported exact-track edge",
                }
            )
        edge_diagnostics.append(
            {
                "sequence": sequence,
                "distance_m": distance_m,
                "country": country,
                "start_candidates": start_candidates,
                "end_candidates": end_candidates,
                "reason": reason,
            }
        )

    totals = {"Highway": 0.0, "Country": 0.0, "City": 0.0, "Unresolved": unresolved_distance}
    country_results: list[dict[str, Any]] = []
    sources: list[str] = []
    matched_maneuvers = 0
    for country, edges in by_country.items():
        if not edges:
            continue
        distance_m = sum(float(edge["distance_m"]) for edge in edges)
        if progress:
            progress(
                f"Analyzing {distance_m / 1000:.3f} exact-track km assigned conservatively to "
                f"{SUPPORTED_COUNTRIES[country]}..."
            )
        try:
            package = analyzer(_subroute(route, country, edges), progress=progress)
        except (DenmarkAdapterError, OfficialAdapterError) as exc:
            package = None
            error = str(exc)
        else:
            error = None
        if package is None:
            totals["Unresolved"] += distance_m
            country_results.append(
                {
                    "country": country,
                    "track_distance_m": distance_m,
                    "status": "unresolved",
                    "error": error or "No verified adapter evidence was returned.",
                }
            )
            continue
        for row in package["breakdown"]:
            totals[str(row["category"])] += float(row["distance_m"])
        matched_maneuvers += int(package.get("matched_maneuvers") or 0)
        sources.extend(str(item) for item in package.get("official_sources", []))
        country_results.append(
            {
                "country": country,
                "track_distance_m": distance_m,
                "status": "classified" if float(package.get("matched_distance_m") or 0) > 0 else "unresolved",
                "adapter": package.get("adapter"),
                "matched_distance_m": float(package.get("matched_distance_m") or 0),
                "breakdown": package["breakdown"],
                "error": None,
            }
        )

    track_total = sum(totals.values())
    google_total = float(route["distance_m"])
    if track_total <= 0:
        raise OfficialAdapterError("The imported exact track had no usable distance.")
    scale = google_total / track_total
    totals = {category: max(0.0, distance * scale) for category, distance in totals.items()}
    matched_distance = sum(totals[category] for category in ("Highway", "Country", "City"))
    if matched_distance <= 0:
        raise OfficialAdapterError("No cross-border exact-track edge received verified official road evidence.")

    rows = [
        {
            "category": category,
            "official_layer": (
                "Country-specific official adapters dispatched from GISCO country-boundary guards"
                if category != "Unresolved"
                else "Border-overlap, transition, unsupported, failed, or inconclusive exact-track edges"
            ),
            "distance_m": totals[category],
            "status": (
                "Official country evidence matched; country dispatch and Google-total reconciliation inferred"
                if category != "Unresolved" and totals[category] > 0.01
                else "No matched segments" if category != "Unresolved" else "Unresolved"
            ),
        }
        for category in ("Highway", "Country", "City", "Unresolved")
    ]
    now = datetime.now(timezone.utc)
    return {
        "schema_version": 2,
        "name": "Runtime cross-border exact-track analysis",
        "analysis_date": now.date().isoformat(),
        "country": "MULTI",
        "adapter": CROSS_BORDER_VERSION,
        "runtime": True,
        "summary": "Conservative cross-border dispatch from reconciled imported exact-track geometry",
        "route_identity": {
            "route_fingerprint": route["route_fingerprint"],
            "geometry_hash": route["geometry_hash"],
            "distance_m": google_total,
            "maneuver_count": route["maneuver_count"],
        },
        "breakdown": rows,
        "matched_maneuvers": matched_maneuvers,
        "matched_distance_m": matched_distance,
        "country_results": country_results,
        "diagnostics": edge_diagnostics[:500],
        "performance": {
            "track_edge_count": len(points) - 1,
            "country_assigned_edge_count": sum(len(edges) for edges in by_country.values()),
            "unresolved_edge_count": sum(item["country"] is None for item in edge_diagnostics),
            "country_adapter_count": len(country_results),
        },
        "inferred": [
            "Imported exact-track edges were dispatched only when all points sampled at no more than 2 km intervals passed one unique supported-country GISCO boundary guard.",
            "GISCO geometry is an official statistical reference dataset, not a legal border determination.",
            "Country-adapter track distances were scaled uniformly to Google's reconciled authoritative route total.",
        ],
        "unresolved": [
            f"{totals['Unresolved'] / 1000:.3f} km remained unresolved after Google-total reconciliation.",
            "Edges at transitions, boundary ambiguities, unsupported countries, or failed adapters were not redistributed.",
        ],
        "method": (
            "RoadProof reconciled the imported exact track with Google maneuver endpoints and distance, split only edges "
            "whose sampled points shared one unambiguous GISCO supported-country dispatch guard, ran each country-specific official "
            "adapter, retained all ambiguous or failed edges as unresolved, and uniformly reconciled the track allocation "
            "to Google's authoritative route total. No legal-boundary or Google-polyline equivalence claim is made."
        ),
        "official_sources": sorted(set(sources + ["Eurostat GISCO Countries 2024, 1:1M, EPSG:4326"])),
    }
