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
import time
from typing import Any, Callable

from .denmark import _haversine


MAX_WORKERS = 6
SAMPLE_SPACING_M = 750.0
MAX_SAMPLES_PER_MANEUVER = 11
MAX_TRACK_EDGES = 1_000


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
    started = time.monotonic()
    countries = {str(value).upper() for value in route.get("countries", []) if value}
    if countries != {country} or not route.get("maneuvers"):
        return None

    track_points = route.get("_exact_track_points")
    reconciliation = route.get("track_reconciliation") or {}
    use_track = bool(track_points) and reconciliation.get("status") == "consistent_not_equivalent"
    if use_track and len(track_points) - 1 > MAX_TRACK_EDGES:
        raise OfficialAdapterError(
            f"The exact track has {len(track_points) - 1:,} edges; simplify it to at most "
            f"{MAX_TRACK_EDGES:,} edges."
        )
    if use_track:
        analysis_units = [
            {
                "sequence": sequence,
                "distance_m": _haversine(start, end),
                "start_lat": start[0],
                "start_lon": start[1],
                "end_lat": end[0],
                "end_lon": end[1],
            }
            for sequence, (start, end) in enumerate(zip(track_points, track_points[1:]))
        ]
        source_label = "exact-track edge"
    else:
        reconstructed = route.get("_reconstructed_maneuver_points") or {}
        analysis_units = []
        for fallback_sequence, maneuver in enumerate(route["maneuvers"]):
            sequence = int(maneuver.get("sequence", fallback_sequence))
            points = reconstructed.get(sequence)
            if not points:
                analysis_units.append(maneuver)
                continue
            edge_distances = [_haversine(start, end) for start, end in zip(points, points[1:])]
            geometry_total = sum(edge_distances)
            for edge_index, (start, end, edge_distance) in enumerate(zip(points, points[1:], edge_distances)):
                analysis_units.append({
                    "sequence": sequence,
                    "edge_sequence": edge_index,
                    "distance_m": float(maneuver["distance_m"]) * edge_distance / geometry_total,
                    "start_lat": start[0], "start_lon": start[1], "end_lat": end[0], "end_lon": end[1],
                    "reconstructed": True,
                })
        source_label = "route-geometry" if reconstructed else "maneuver"
    analysis_total = sum(float(unit["distance_m"]) for unit in analysis_units)
    if analysis_total <= 0 or not math.isfinite(analysis_total):
        raise OfficialAdapterError(f"The {source_label} geometry had no finite positive distance.")

    prepared: list[tuple[dict[str, Any], list[tuple[float, float]], str | None]] = []
    unique_points: dict[tuple[float, float], tuple[float, float]] = {}
    for maneuver in analysis_units:
        points, gate_error = _sample_points(maneuver)
        prepared.append((maneuver, points, gate_error))
        for lat, lon in points:
            unique_points[(round(lat, 7), round(lon, 7))] = (lat, lon)

    if progress:
        progress(
            f"Querying {len(unique_points):,} conservative {source_label} sample(s) "
            f"against official {country_name} road data..."
        )

    results: dict[tuple[float, float], PointEvidence] = {}
    errors: list[str] = []
    error_counts: dict[str, int] = {}
    systemic_error: str | None = None
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
                    error = str(exc)
                    errors.append(error)
                    error_counts[error] = error_counts.get(error, 0) + 1
                    results[key] = PointEvidence("Unresolved", "Official service unavailable", error)
                    if error_counts[error] >= 3:
                        systemic_error = error
                        for pending in futures:
                            if not pending.done():
                                pending.cancel()
                        break
        if systemic_error:
            for key in unique_points:
                results.setdefault(
                    key,
                    PointEvidence("Unresolved", "Official service unavailable", systemic_error),
                )

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
                "edge_sequence": maneuver.get("edge_sequence"),
                "geometry_source": "osrm_reconstructed_not_google_equivalent" if maneuver.get("reconstructed") else "google_maneuver_chord",
                "google_distance_m": distance_m * (float(route["distance_m"]) / analysis_total) if use_track else distance_m,
                "track_distance_m": distance_m if use_track else None,
                "matched": category != "Unresolved",
                "category": category,
                "sample_count": len(samples),
                "sample_categories": [sample.category for sample in samples],
                "reason": reason,
            }
        )

    route_total = float(route["distance_m"])
    allocated_total = sum(totals.values())
    if route_total < 0 or not math.isfinite(route_total):
        raise OfficialAdapterError("The Google route total was not a finite non-negative distance.")
    if allocated_total > 0:
        # Google permits a small route-total/maneuver-total discrepancy. Keep
        # its route total authoritative and reconcile every bucket by the same
        # factor so no category can become negative or exceed the total.
        scale = route_total / allocated_total
        totals = {category: max(0.0, distance * scale) for category, distance in totals.items()}
    else:
        totals["Unresolved"] = route_total
    matched_distance = sum(totals[category] for category in ("Highway", "Country", "City"))
    if matched_distance <= 0:
        details = f" First service error: {errors[0]}" if errors else ""
        raise OfficialAdapterError(
            f"No {source_label} passed the official {country_name} point-evidence gates.{details}"
        )

    rows = [
        {
            "category": category,
            "official_layer": (
                "Exact-track edges with missing, mixed, or inconclusive official samples"
                if use_track and category == "Unresolved"
                else official_layers[category]
            ),
            "distance_m": totals[category],
            "status": (
                "Official point evidence matched; exact-track sampling and Google-total reconciliation inferred"
                if use_track and category != "Unresolved" and totals[category] > 0.01
                else "Official point evidence matched; route interpolation and Google distance allocation inferred"
                if category != "Unresolved" and totals[category] > 0.01
                else "No matched segments"
                if category != "Unresolved"
                else "Unresolved"
            ),
        }
        for category in ("Highway", "Country", "City", "Unresolved")
    ]
    unresolved_details = [item for item in diagnostics if not item["matched"]]
    unresolved_sequences = sorted({int(item["sequence"]) for item in unresolved_details})
    matched_sequences = {
        int(item["sequence"])
        for item in diagnostics
        if not any(int(other["sequence"]) == int(item["sequence"]) and not other["matched"] for other in diagnostics)
    }
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
            "geometry_hash": route.get("geometry_hash") if use_track else None,
            "distance_m": route["distance_m"],
            "maneuver_count": route["maneuver_count"],
        },
        "breakdown": rows,
        "analysis_basis": (
            "reconciled_imported_exact_track" if use_track
            else "hybrid_osrm_reconstructed_maneuvers" if route.get("_reconstructed_maneuver_points")
            else "google_maneuver_chords"
        ),
        "geometry_source": route.get("automatic_geometry_status"),
        "matched_maneuvers": len(matched_sequences) if not use_track else 0,
        "matched_track_edges": matched_count if use_track else 0,
        "matched_distance_m": matched_distance,
        "performance": {
            "analysis_seconds": round(time.monotonic() - started, 3),
            "maneuver_count": len(route["maneuvers"]),
            "track_edge_count": len(prepared) if use_track else 0,
            "gate_rejected_maneuvers": 0 if use_track else sum(gate_error is not None for _, _, gate_error in prepared),
            "unique_sample_points": len(unique_points),
            "classified_sample_points": sum(
                sample.category != "Unresolved" for sample in results.values()
            ),
            "service_error_count": len(errors),
            "systemic_failure_short_circuit": systemic_error is not None,
        },
        "diagnostics": diagnostics,
        "inferred": [
            (
                "This adapter uses unanimous official samples along a separately imported exact track that "
                "reconciled with Google distance and endpoints; consistency does not prove Google-track equivalence."
                if use_track
                else "This adapter uses per-maneuver OSRM geometry only where its distance reconciled with Google; "
                "the reconstruction is not claimed to be Google's selected geometry."
                if route.get("_reconstructed_maneuver_points")
                else "This adapter uses unanimous official samples along a reconciled maneuver chord; "
                "it does not claim that the chord is Google's hidden full polyline."
            ),
            (
                f"{matched_count} of {len(prepared)} exact-track edges passed the unanimous-sample gates."
                if use_track
                else f"{len(matched_sequences)} of {route['maneuver_count']} maneuvers passed all applicable official-sample gates."
            ),
            mapping_note,
        ],
        "unresolved": [
            f"{totals['Unresolved'] / 1000:.3f} km remained unresolved across "
            f"{len(unresolved_details) if use_track else len(unresolved_sequences)} "
            f"{'exact-track edge(s)' if use_track else 'maneuver(s)' }.",
            (
                "Exact-track edges with a missing official feature or mixed sample classes were not redistributed."
                if use_track
                else "Maneuvers with a curved/indirect chord, missing official feature, or mixed sample "
                "classes were not redistributed."
            ),
            *(
                f"{'Track edge' if use_track else 'Maneuver'} {item['sequence'] + 1}: {item['reason']}."
                for item in (
                    unresolved_details[:12] if use_track else
                    [next(detail for detail in unresolved_details if int(detail["sequence"]) == sequence) for sequence in unresolved_sequences[:12]]
                )
            ),
        ],
        "method": (
            (
                f"RoadProof sampled each segment of a separately imported exact track against {adapter}. "
                "An edge was retained only when every official sample returned the same decisive class. "
                "Track-derived category totals were reconciled uniformly to Google's authoritative route total. "
                "Missing, mixed, or inconclusive evidence remained unresolved; no equivalence claim was made."
                if use_track
                else f"RoadProof sampled accepted per-maneuver OSRM reconstructions and retained Google maneuver "
                f"chords for rejected candidates against {adapter}. Each OSRM leg was accepted only when its distance "
                "reconciled independently with the corresponding Google maneuver; no geometry equivalence is claimed."
                if route.get("_reconstructed_maneuver_points")
                else f"RoadProof sampled the Google maneuver anchors and interpolated chord against {adapter}. "
                "A maneuver was retained only when Google distance reconciled with the chord and every official "
                "sample returned the same decisive class. Its exact Google distance was then assigned to that "
                "class. Curved, missing, mixed, or inconclusive evidence remained unresolved."
            )
        ),
        "official_sources": [*official_sources, f"Runtime retrieval: {now.isoformat(timespec='seconds')}"],
    }
