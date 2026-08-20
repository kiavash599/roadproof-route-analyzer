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
]
