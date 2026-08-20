"""Country-adapter registry for runtime official-road evidence."""

from __future__ import annotations

from typing import Any, Callable

from .belgium import analyze_belgium_route
from .denmark import DenmarkAdapterError, analyze_denmark_route
from .germany import analyze_germany_route
from .sampled import OfficialAdapterError
from .sweden import analyze_sweden_route


Progress = Callable[[str], None]
SUPPORTED_COUNTRIES = {
    "BE": "Belgium",
    "DE": "Germany",
    "DK": "Denmark",
    "SE": "Sweden",
}

# These broad envelopes are only a conservative dispatch guard when Google's
# private response omits its country code.  They are not road-class evidence or
# legal borders.  Any overlap remains ambiguous unless a country name exposed
# by the route URL selects exactly one envelope.
COUNTRY_ENVELOPES = {
    "BE": (49.45, 51.55, 2.45, 6.50),
    "DE": (47.20, 55.15, 5.80, 15.10),
    "DK": (54.40, 57.90, 7.70, 15.30),
    "SE": (55.20, 69.20, 10.50, 24.30),
}
COUNTRY_LABELS = {
    "BE": ("belgium", "belgique", "belgië", "belgien"),
    "DE": ("germany", "deutschland", "allemagne", "tyskland"),
    "DK": ("denmark", "danmark", "dänemark", "danemark"),
    "SE": ("sweden", "sverige", "schweden", "suède"),
}


def _route_points(route: dict[str, Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for maneuver in route.get("maneuvers", []):
        try:
            points.extend(
                [
                    (float(maneuver["start_lat"]), float(maneuver["start_lon"])),
                    (float(maneuver["end_lat"]), float(maneuver["end_lon"])),
                ]
            )
        except (KeyError, TypeError, ValueError):
            return []
    return points


def _inside_envelope(point: tuple[float, float], envelope: tuple[float, float, float, float]) -> bool:
    latitude, longitude = point
    south, north, west, east = envelope
    return south <= latitude <= north and west <= longitude <= east


def resolve_country_signal(route: dict[str, Any]) -> list[str]:
    """Resolve a safe adapter dispatch signal without treating it as evidence."""
    supplied = sorted({str(value).upper() for value in route.get("countries", []) if value})
    if supplied:
        route["country_signal_source"] = "Google payload"
        route["countries"] = supplied
        return supplied

    points = _route_points(route)
    if not points:
        route["country_signal_source"] = "Google omitted; no usable coordinate fallback"
        return []
    containing = {
        country
        for country, envelope in COUNTRY_ENVELOPES.items()
        if all(_inside_envelope(point, envelope) for point in points)
    }
    labels = " ".join(
        str(route.get(field) or "").casefold()
        for field in ("origin_name", "destination_name")
    )
    named = {
        country
        for country, names in COUNTRY_LABELS.items()
        if any(name in labels for name in names)
    }
    candidates = containing & named if named else containing
    if len(candidates) == 1:
        country = next(iter(candidates))
        route["countries"] = [country]
        route["country_signal_source"] = (
            "URL country label + coordinate guard" if named else "unique coordinate-envelope fallback"
        )
        return [country]
    route["country_signal_source"] = "Google omitted; conservative fallback remained ambiguous"
    return []


def analyze_supported_route(
    route: dict[str, Any],
    *,
    progress: Progress | None = None,
) -> dict[str, Any] | None:
    """Dispatch an all-one-country route to its versioned official adapter."""
    countries = {str(value).upper() for value in route.get("countries", []) if value}
    if not countries:
        # Preserve the Denmark adapter's conservative geographic fallback for
        # older Google payloads that did not expose a country code.
        return analyze_denmark_route(route, progress=progress)
    if len(countries) != 1:
        return None
    country = next(iter(countries))
    analyzers = {
        "BE": analyze_belgium_route,
        "DE": analyze_germany_route,
        "DK": analyze_denmark_route,
        "SE": analyze_sweden_route,
    }
    analyzer = analyzers.get(country)
    return analyzer(route, progress=progress) if analyzer else None


__all__ = [
    "DenmarkAdapterError",
    "OfficialAdapterError",
    "SUPPORTED_COUNTRIES",
    "analyze_supported_route",
    "resolve_country_signal",
]
