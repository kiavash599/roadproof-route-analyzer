"""Resolve a Google Maps link and read its selected directions response.

This module does not send the stops to a second routing engine. It follows the
shared Google Maps link and reads the directions preload chosen by Google Maps.
"""

from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import json
import math
import re
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urljoin, urlparse
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SHORT_GOOGLE_HOSTS = {
    "maps.app.goo.gl",
    "goo.gl",
}
GOOGLE_COUNTRY_DOMAINS = {
    "google.at",
    "google.be",
    "google.ch",
    "google.co.uk",
    "google.com",
    "google.cz",
    "google.de",
    "google.dk",
    "google.es",
    "google.fi",
    "google.fr",
    "google.ie",
    "google.it",
    "google.nl",
    "google.no",
    "google.pl",
    "google.pt",
    "google.se",
}
ALLOWED_INPUT_HOSTS = {
    *SHORT_GOOGLE_HOSTS,
    "google.com",
    "www.google.com",
    "maps.google.com",
    *(f"www.{domain}" for domain in GOOGLE_COUNTRY_DOMAINS),
    *(f"maps.{domain}" for domain in GOOGLE_COUNTRY_DOMAINS),
    *GOOGLE_COUNTRY_DOMAINS,
}
URL_IN_TEXT_RE = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)


class RouteReadError(RuntimeError):
    """Raised when the selected Google route cannot be read defensibly."""


def _validate_redirect_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RouteReadError("Google returned a redirect with an invalid port.") from exc
    if (
        parsed.scheme == "https"
        and host == "consent.google.com"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    ):
        return
    try:
        _validate_input_url(url)
    except RouteReadError as exc:
        raise RouteReadError("Google attempted to redirect outside the allowed Maps hosts.") from exc


class SafeGoogleRedirectHandler(HTTPRedirectHandler):
    """Reject a redirect before urllib sends a request to an unexpected host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_redirect_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DirectionsPreloadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link" or self.href is not None:
            return
        values = {key.lower(): value for key, value in attrs}
        href = values.get("href") or ""
        rel = (values.get("rel") or "").lower().split()
        if "preload" in rel and values.get("as") == "fetch" and "/maps/preview/directions?" in href:
            self.href = html.unescape(href)


def _validate_input_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RouteReadError("The Google Maps URL contains an invalid port.") from exc
    if (
        parsed.scheme.lower() != "https"
        or host not in ALLOWED_INPUT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RouteReadError(
            "Use an HTTPS Google Maps route link (for example https://maps.app.goo.gl/...)."
        )
    if host in SHORT_GOOGLE_HOSTS:
        if parsed.path in ("", "/"):
            raise RouteReadError("The short Google Maps link is incomplete.")
    elif not parsed.path.startswith("/maps"):
        raise RouteReadError("The URL opens Google, but it is not a Google Maps link.")


def normalize_google_maps_url(value: str) -> str:
    """Extract and validate one pasted Google Maps HTTPS URL.

    Clipboard contents sometimes include surrounding quotes, angle brackets,
    a descriptive label, or a trailing full stop. Only the URL is retained.
    """
    text = html.unescape(str(value or "")).strip()
    if len(text) >= 2 and (text[0], text[-1]) in {
        ('"', '"'),
        ("'", "'"),
        ("<", ">"),
        ("(", ")"),
        ("[", "]"),
    }:
        text = text[1:-1].strip()
    match = URL_IN_TEXT_RE.search(text)
    if match is None:
        raise RouteReadError("No HTTPS Google Maps URL was found in the pasted text.")
    url = match.group(0).rstrip(".,;")
    _validate_input_url(url)
    return url


def route_endpoints_from_url(url: str) -> tuple[str | None, str | None]:
    """Return human-readable origin/destination labels when the URL exposes them."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    origin = next(iter(query.get("origin", [])), None)
    destination = next(iter(query.get("destination", [])), None)
    if origin and destination:
        return _strip_markup(origin), _strip_markup(destination)

    parts = [unquote_plus(part).strip() for part in parsed.path.split("/")]
    try:
        direction_index = parts.index("dir")
    except ValueError:
        return None, None
    candidates: list[str] = []
    for part in parts[direction_index + 1 :]:
        if not part:
            continue
        if part.startswith("@") or part.startswith("data="):
            break
        if part.startswith("!"):
            continue
        candidates.append(_strip_markup(part))
    if len(candidates) < 2:
        return None, None
    return candidates[0], candidates[-1]


def _strip_markup(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _number(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _coord_from_step(step: list[Any]) -> tuple[float, float]:
    coord = step[0][7][2]
    return float(coord[2]), float(coord[3])


def _destination_from_leg(leg: list[Any]) -> tuple[float, float]:
    coord = leg[4][2][2]
    return float(coord[2]), float(coord[3])


def route_fingerprint(distance_m: int, maneuvers: list[dict[str, Any]]) -> str:
    """Hash distance plus maneuver anchors, independent of link text/language."""
    normalized = {
        "distance_m": distance_m,
        "maneuvers": [
            {
                "distance_m": item["distance_m"],
                "start": [round(item["start_lat"], 5), round(item["start_lon"], 5)],
                "end": [round(item["end_lat"], 5), round(item["end_lon"], 5)],
            }
            for item in maneuvers
        ],
    }
    body = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def parse_directions_response(body: bytes, *, input_url: str, resolved_url: str) -> dict[str, Any]:
    response_sha256 = hashlib.sha256(body).hexdigest()
    raw = body
    if raw.startswith(b")]}'"):
        raw = raw.split(b"\n", 1)[1]
    try:
        root = json.loads(raw)
        route = root[0][1][0]
        header = route[0]
        legs = route[1]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise RouteReadError("Google returned a response, but its route structure was not recognized.") from exc

    flattened: list[tuple[int, int, int, list[Any]]] = []
    try:
        for leg_index, leg in enumerate(legs):
            for group_index, group in enumerate(leg[1]):
                for step_index, step in enumerate(group[1]):
                    flattened.append((leg_index, group_index, step_index, step))
    except (IndexError, TypeError) as exc:
        raise RouteReadError("The route contains no readable driving maneuvers.") from exc

    records: list[dict[str, Any]] = []
    for sequence, (leg_index, group_index, step_index, step) in enumerate(flattened):
        try:
            start_lat, start_lon = _coord_from_step(step)
            if sequence + 1 < len(flattened) and flattened[sequence + 1][0] == leg_index:
                end_lat, end_lon = _coord_from_step(flattened[sequence + 1][3])
            else:
                end_lat, end_lon = _destination_from_leg(legs[leg_index])
            core = step[0]
            records.append(
                {
                    "sequence": sequence,
                    "leg": leg_index,
                    "group": group_index,
                    "step": step_index,
                    "distance_m": _number(core[2][0]),
                    "duration_s": _number(core[3][0]),
                    "start_lat": start_lat,
                    "start_lon": start_lon,
                    "end_lat": end_lat,
                    "end_lon": end_lon,
                    "instruction": _strip_markup(core[1]),
                }
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise RouteReadError(f"Google maneuver {sequence + 1} could not be parsed safely.") from exc

    distance_m = _number(header[2][0])
    maneuver_sum_m = sum(item["distance_m"] for item in records)
    if not distance_m or not records:
        raise RouteReadError("Google did not return a usable driving-route distance.")
    if abs(distance_m - maneuver_sum_m) > max(10, distance_m * 0.001):
        raise RouteReadError(
            f"Google route total ({distance_m} m) does not reconcile with its maneuvers "
            f"({maneuver_sum_m} m). No report was created."
        )

    leg_rows = []
    for index, leg in enumerate(legs):
        try:
            leg_rows.append(
                {
                    "leg": index + 1,
                    "distance_m": _number(leg[0][2][0]),
                    "duration_s": _number(leg[0][3][0]),
                }
            )
        except (IndexError, TypeError):
            leg_rows.append({"leg": index + 1, "distance_m": 0, "duration_s": 0})

    origin_name, destination_name = route_endpoints_from_url(resolved_url)
    return {
        "input_url": input_url,
        "resolved_url": resolved_url,
        "origin_name": origin_name,
        "destination_name": destination_name,
        "route_name": str(header[1] or "Google-selected route"),
        "distance_m": distance_m,
        "duration_s": _number(header[3][0]),
        "maneuver_count": len(records),
        "maneuver_sum_m": maneuver_sum_m,
        "legs": leg_rows,
        "maneuvers": records,
        "response_sha256": response_sha256,
        "route_fingerprint": route_fingerprint(distance_m, records),
    }


def fetch_google_route(url: str, *, timeout: int = 60) -> dict[str, Any]:
    input_url = normalize_google_maps_url(url)
    opener = build_opener(HTTPCookieProcessor(CookieJar()), SafeGoogleRedirectHandler())
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        page_response = opener.open(Request(input_url, headers=headers), timeout=timeout)
        page_url = page_response.geturl()
        page_text = page_response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RouteReadError(f"Could not open the Google Maps link: {exc}") from exc

    # In the EEA, anonymous requests can be redirected to Google's consent
    # interstitial. Follow its allowlisted `continue` target with a non-tracking
    # consent cookie so the public route page can be read without a browser or
    # Google account.
    consent_cookie = "CONSENT=YES+cb.20220419-08-p0.en+FX+111"
    if (urlparse(page_url).hostname or "").lower() == "consent.google.com":
        continue_values = parse_qs(urlparse(page_url).query).get("continue", [])
        if not continue_values:
            raise RouteReadError("Google's consent page did not contain a safe route continuation.")
        continue_url = continue_values[0]
        continue_parsed = urlparse(continue_url)
        if (
            continue_parsed.scheme != "https"
            or (continue_parsed.hostname or "").lower() not in {"google.com", "www.google.com", "maps.google.com"}
            or not continue_parsed.path.startswith("/maps/")
        ):
            raise RouteReadError("Google's consent page returned an unexpected continuation target.")
        try:
            page_response = opener.open(
                Request(continue_url, headers={**headers, "Cookie": consent_cookie}),
                timeout=timeout,
            )
            page_url = page_response.geturl()
            page_text = page_response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RouteReadError(f"Could not continue from Google's consent page: {exc}") from exc

    try:
        _validate_input_url(page_url)
    except RouteReadError as exc:
        raise RouteReadError("The Google Maps link redirected to an unexpected destination.") from exc

    parser = DirectionsPreloadParser()
    parser.feed(page_text)
    if parser.href is None:
        raise RouteReadError(
            "Google Maps opened, but no selected driving-route payload was exposed. "
            "Open the link in Google Maps, confirm it is a driving route, share it again, and retry."
        )
    endpoint = urljoin("https://www.google.com/", parser.href)
    endpoint_parsed = urlparse(endpoint)
    if (
        endpoint_parsed.scheme != "https"
        or (endpoint_parsed.hostname or "").lower() not in ALLOWED_INPUT_HOSTS
        or not endpoint_parsed.path.startswith("/maps/preview/directions")
    ):
        raise RouteReadError("Google exposed an unexpected directions endpoint.")
    try:
        response = opener.open(
            Request(
                endpoint,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": page_url,
                    "Accept": "*/*",
                    "Cookie": consent_cookie,
                },
            ),
            timeout=timeout,
        )
        body = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RouteReadError(f"Could not read Google's selected route payload: {exc}") from exc
    return parse_directions_response(body, input_url=input_url, resolved_url=page_url)
