"""Versioned downstream profiles evaluated independently of road evidence."""

from __future__ import annotations

from typing import Any


PROFILE_SCHEMA = "roadproof.profile-result.v1"


def evaluate_profile(profile: str, total_m: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if profile == "composition":
        return {
            "schema": PROFILE_SCHEMA,
            "id": "composition",
            "version": "1",
            "state": "not_evaluated",
            "checks": [],
        }

    distances = {str(row["category"]): float(row["distance_m"]) for row in rows}
    unresolved = distances.get("Unresolved", 0.0)
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "check": "overall_distance",
            "measured_m": total_m,
            "target_m": 400_000.0,
            "state": "meets" if total_m >= 400_000 else "fails",
            "short_by_m": max(0.0, 400_000.0 - total_m),
        }
    )
    for category in ("Highway", "Country", "City"):
        measured = distances.get(category, 0.0)
        if measured >= 100_000:
            state = "meets"
        elif unresolved > 0 and measured + unresolved >= 100_000:
            state = "insufficient_evidence"
        else:
            state = "fails"
        checks.append(
            {
                "check": category.casefold(),
                "measured_m": measured,
                "target_m": 100_000.0,
                "state": state,
                "short_by_m": max(0.0, 100_000.0 - measured) if state == "fails" else None,
                "unresolved_route_m": unresolved,
            }
        )
    checks.append(
        {
            "check": "darkness",
            "measured_m": None,
            "target_m": 60_000.0,
            "state": "not_measured",
            "short_by_m": None,
        }
    )
    states = {check["state"] for check in checks}
    overall_state = "fails" if "fails" in states else "meets" if states == {"meets"} else "incomplete"
    return {
        "schema": PROFILE_SCHEMA,
        "id": "eu-isa-2021-1958",
        "version": "2023-09-21",
        "state": overall_state,
        "checks": checks,
    }


def check_text(check: dict[str, Any]) -> str:
    state = check["state"]
    if state == "meets":
        return "Meets"
    if state == "fails":
        return f"Short by {float(check['short_by_m']) / 1000:.3f} km"
    if state == "insufficient_evidence":
        return "Insufficient evidence"
    if state == "not_measured":
        return "Not measured"
    return str(state).replace("_", " ").title()
