"""Versioned machine-readable RoadProof results."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .profiles import evaluate_profile
from .report import report_filename


RESULT_SCHEMA = "roadproof.result.v1"


def result_document(
    route: dict[str, Any],
    rows: list[dict[str, Any]],
    evidence: dict[str, Any] | None,
    profile: str,
    created_at: datetime,
    *,
    output_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    total_m = float(route["distance_m"])
    return {
        "schema": RESULT_SCHEMA,
        "created_at": created_at.astimezone().isoformat(timespec="seconds"),
        "status": str(route.get("result_status") or "unknown"),
        "route": {
            "name": route["route_name"],
            "origin": route.get("origin_name"),
            "destination": route.get("destination_name"),
            "input_url": route["input_url"],
            "resolved_url": route["resolved_url"],
            "distance_m": total_m,
            "duration_s": int(route["duration_s"]),
            "maneuver_count": int(route["maneuver_count"]),
            "maneuver_distance_m": float(route["maneuver_sum_m"]),
            "legs": route["legs"],
            "countries": sorted({str(value).upper() for value in route.get("countries", []) if value}),
            "country_signal": route.get("country_signal"),
            "route_fingerprint": route["route_fingerprint"],
            "route_fingerprint_version": route.get("route_fingerprint_version", "maneuver-v1"),
            "legacy_route_fingerprint": route.get("legacy_route_fingerprint"),
            "matched_evidence_fingerprint": route.get("matched_evidence_fingerprint"),
            "geometry_hash": route.get("geometry_hash"),
            "geometry_hash_status": route.get("geometry_hash_status", "not_available"),
            "geometry_hash_version": route.get("geometry_hash_version"),
            "exact_track": route.get("exact_track"),
            "track_reconciliation": route.get("track_reconciliation"),
            "automatic_geometry": route.get("automatic_geometry_status"),
            "directions_response_sha256": route["response_sha256"],
        },
        "evidence": {
            "available": evidence is not None,
            "message": route.get("evidence_status"),
            "adapter": evidence.get("adapter") if evidence else None,
            "adapter_country": evidence.get("country") if evidence else None,
            "runtime": bool(evidence.get("runtime")) if evidence else False,
            "matched_maneuvers": evidence.get("matched_maneuvers") if evidence else None,
            "matched_distance_m": evidence.get("matched_distance_m") if evidence else 0.0,
            "breakdown": rows,
            "diagnostics": evidence.get("diagnostics", []) if evidence else [],
            "country_results": evidence.get("country_results", []) if evidence else [],
            "inferred": evidence.get("inferred", []) if evidence else [],
            "unresolved": evidence.get("unresolved", []) if evidence else [],
            "official_sources": evidence.get("official_sources", []) if evidence else [],
        },
        "profile": evaluate_profile(profile, total_m, rows),
        "performance": route.get("performance", evidence.get("performance", {}) if evidence else {}),
        "outputs": output_files or {},
    }


def save_json_result(
    output_dir: Path,
    document: dict[str, Any],
    created_at: datetime,
    route: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = output_dir / Path(report_filename(route, created_at)).with_suffix(".json")
    candidate = requested
    collision = 2
    while True:
        document["outputs"]["json"] = str(candidate.resolve())
        try:
            with candidate.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            return candidate.resolve()
        except FileExistsError:
            candidate = requested.with_name(f"{requested.stem}-{collision}{requested.suffix}")
            collision += 1
