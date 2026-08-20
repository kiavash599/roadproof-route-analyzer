"""Conservative official point-evidence matching for country adapters.

Some official road portals expose current attributes at a coordinate but do
not expose a routable public graph.  This module samples only maneuvers whose
straight chord reconciles with Google's distance, requires every sample to
agree, and leaves every other metre unresolved.  It deliberately does not
pretend that point samples are an exact route polyline.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable

from .denmark import _haversine


MAX_WORKERS = 6
SAMPLE_SPACING_M = 750.0
MAX_SAMPLES_PER_MANEUVER = 11


class OfficialAdapterError(RuntimeError):
    """Raised when a supported official source cannot be read safely."""


@dataclass(frozen=True)
class PointEvidence:
    category: str
    layer: str
    detail: str = ""


PointClassifier = Callable[[float, float], PointEvidence]
Progress = Callable[[str], None]


def _sample_points(maneuver: dict[str, Any]) -> tuple[list[tuple[float, float]], str | None]:
    start = (float(maneuver["start_lat"]), float(maneuver["start_lon"]))
    end = (float(maneuver["end_lat"]), float(maneuver["end_lon"]))
    distance_m = max(0.0, float(maneuver["distance_m"]))
    chord_m = _haversine(start, end)
    difference_m = abs(distance_m - chord_m)
    if chord_m < 5.0 and distance_m > 50.0:
        return [], "maneuver anchors do not expose a usable route chord"
    if difference_m > max(200.0, distance_m * 0.12):
        return [], (
            f"Google distance and maneuver chord differed by {difference_m:.0f} m; "
            "point interpolation was not safe"
        )

    intervals = max(2, min(MAX_SAMPLES_PER_MANEUVER - 1, math.ceil(max(distance_m, chord_m) / SAMPLE_SPACING_M)))
    points = [
        (
            start[0] + (end[0] - start[0]) * index / intervals,
            start[1] + (end[1] - start[1]) * index / intervals,
        )
        for index in range(intervals + 1)
    ]
    return points, None


def analyze_sampled_route(
    route: dict[str, Any],
    *,
    country: str,
    country_name: str,
    adapter: str,
    classifier: PointClassifier,
    official_layers: dict[str, str],
    official_sources: list[str],
    mapping_note: str,
    progress: Progress | None = None,
) -> dict[str, Any] | None:
    """Build an evidence package from unanimous official point samples."""
    countries = {str(value).upper() for value in route.get("countries", []) if value}
    if countries != {country} or not route.get("maneuvers"):
        return None

    prepared: list[tuple[dict[str, Any], list[tuple[float, float]], str | None]] = []
    unique_points: dict[tuple[float, float], tuple[float, float]] = {}
    for maneuver in route["maneuvers"]:
        points, gate_error = _sample_points(maneuver)
        prepared.append((maneuver, points, gate_error))
        for lat, lon in points:
            unique_points[(round(lat, 7), round(lon, 7))] = (lat, lon)

    if progress:
        progress(
            f"Querying {len(unique_points):,} conservative route sample(s) "
            f"against official {country_name} road data..."
        )

    results: dict[tuple[float, float], PointEvidence] = {}
    errors: list[str] = []
    if unique_points:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(classifier, lat, lon): key
                for key, (lat, lon) in unique_points.items()
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except OfficialAdapterError as exc:
                    errors.append(str(exc))
                    results[key] = PointEvidence("Unresolved", "Official service unavailable", str(exc))

    totals = {"Highway": 0.0, "Country": 0.0, "City": 0.0, "Unresolved": 0.0}
    diagnostics: list[dict[str, Any]] = []
    matched_count = 0
    for fallback_sequence, (maneuver, points, gate_error) in enumerate(prepared):
        sequence = int(maneuver.get("sequence", fallback_sequence))
        distance_m = float(maneuver["distance_m"])
        if gate_error:
            category = "Unresolved"
            reason = gate_error
            samples: list[PointEvidence] = []
        else:
            samples = [results[(round(lat, 7), round(lon, 7))] for lat, lon in points]
            categories = {sample.category for sample in samples}
            if len(categories) == 1 and "Unresolved" not in categories:
                category = next(iter(categories))
                reason = "all official samples agreed"
                matched_count += 1
            else:
                category = "Unresolved"
                reason = "official samples were missing, mixed, or inconclusive"
        totals[category] += distance_m
        diagnostics.append(
            {
                "sequence": sequence,
                "google_distance_m": distance_m,
                "matched": category != "Unresolved",
                "category": category,
                "sample_count": len(samples),
                "sample_categories": [sample.category for sample in samples],
                "reason": reason,
            }
        )

    route_total = float(route["distance_m"])
    totals["Unresolved"] += route_total - sum(totals.values())
    if totals["Unresolved"] < 0 and abs(totals["Unresolved"]) < 0.05:
        totals["Unresolved"] = 0.0
    matched_distance = route_total - totals["Unresolved"]
    if matched_distance <= 0:
        details = f" First service error: {errors[0]}" if errors else ""
        raise OfficialAdapterError(
            f"No maneuver passed the official {country_name} point-evidence gates.{details}"
        )

    rows = [
        {
            "category": category,
            "official_layer": official_layers[category],
            "distance_m": totals[category],
            "status": (
                "Official point evidence matched; route interpolation and Google distance allocation inferred"
                if category != "Unresolved" and totals[category] > 0.01
                else "No matched segments"
                if category != "Unresolved"
                else "Unresolved"
            ),
        }
        for category in ("Highway", "Country", "City", "Unresolved")
    ]
    unresolved_details = [item for item in diagnostics if not item["matched"]]
    now = datetime.now(timezone.utc)
    return {
        "schema_version": 2,
        "name": f"Runtime {country_name} official-road analysis",
        "analysis_date": now.date().isoformat(),
        "country": country,
        "adapter": adapter,
        "runtime": True,
        "summary": f"Runtime conservative match against official {country_name} road data",
        "route_identity": {
            "route_fingerprint": route["route_fingerprint"],
            "distance_m": route["distance_m"],
            "maneuver_count": route["maneuver_count"],
        },
        "breakdown": rows,
        "matched_maneuvers": matched_count,
        "matched_distance_m": matched_distance,
        "diagnostics": diagnostics,
        "inferred": [
            "This adapter uses unanimous official samples along a reconciled maneuver chord; "
            "it does not claim that the chord is Google's hidden full polyline.",
            f"{matched_count} of {route['maneuver_count']} maneuvers passed the chord and unanimous-sample gates.",
            mapping_note,
        ],
        "unresolved": [
            f"{totals['Unresolved'] / 1000:.3f} km remained unresolved across {len(unresolved_details)} maneuver(s).",
            "Maneuvers with a curved/indirect chord, missing official feature, or mixed sample "
            "classes were not redistributed.",
            *(f"Maneuver {item['sequence'] + 1}: {item['reason']}." for item in unresolved_details[:12]),
        ],
        "method": (
            f"RoadProof sampled the Google maneuver anchors and interpolated chord against {adapter}. "
            "A maneuver was retained only when Google distance reconciled with the chord and every official "
            "sample returned the same decisive class. Its exact Google distance was then assigned to that "
            "class. Curved, missing, mixed, or inconclusive evidence remained unresolved."
        ),
        "official_sources": [*official_sources, f"Runtime retrieval: {now.isoformat(timespec='seconds')}"],
    }
