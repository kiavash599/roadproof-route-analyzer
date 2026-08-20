"""Shared secure-network helpers for Google and official-data adapters."""

from __future__ import annotations

import os
from pathlib import Path
import ssl
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
import json
import random
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CA_BUNDLE_ENV = "ROADPROOF_CA_BUNDLE"
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_HOST_LIMITS: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_HOST_LIMITS_LOCK = threading.Lock()
_OFFLINE: ContextVar[bool] = ContextVar("roadproof_offline", default=False)
_OFFLINE_LOCK = threading.Lock()
_OFFLINE_EVENT = threading.Event()
_OFFLINE_USERS = 0
_METRICS_LOCK = threading.Lock()
_METRICS: dict[str, float] = {
    "request_attempts": 0,
    "retries": 0,
    "request_failures": 0,
    "downloaded_bytes": 0,
    "network_seconds": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_writes": 0,
    "cache_write_failures": 0,
}


class NetworkRequestError(RuntimeError):
    """A bounded HTTPS request could not be completed safely."""


def _metric(name: str, amount: float = 1) -> None:
    with _METRICS_LOCK:
        _METRICS[name] += amount


def reset_performance_metrics() -> None:
    with _METRICS_LOCK:
        for name in _METRICS:
            _METRICS[name] = 0


def performance_metrics() -> dict[str, int | float]:
    with _METRICS_LOCK:
        result = dict(_METRICS)
    result["network_seconds"] = round(result["network_seconds"], 3)
    return {
        name: int(value) if name != "network_seconds" else value
        for name, value in result.items()
    }


@contextmanager
def offline_requests(enabled: bool = True):
    """Prevent official-service network access while allowing cache reads."""
    global _OFFLINE_USERS
    token = _OFFLINE.set(enabled)
    if enabled:
        with _OFFLINE_LOCK:
            _OFFLINE_USERS += 1
            _OFFLINE_EVENT.set()
    try:
        yield
    finally:
        if enabled:
            with _OFFLINE_LOCK:
                _OFFLINE_USERS -= 1
                if _OFFLINE_USERS == 0:
                    _OFFLINE_EVENT.clear()
        _OFFLINE.reset(token)


def secure_ssl_context() -> ssl.SSLContext:
    """Return a verifying TLS context augmented with configured/platform CAs."""
    bundle = os.environ.get(CA_BUNDLE_ENV)
    return _secure_ssl_context(bundle)


@lru_cache(maxsize=4)
def _secure_ssl_context(bundle: str | None) -> ssl.SSLContext:
    if bundle:
        path = Path(bundle).expanduser()
        if not path.is_file():
            raise RuntimeError(f"{CA_BUNDLE_ENV} does not name a readable CA bundle: {path}")

    context = ssl.create_default_context()
    if bundle:
        context.load_verify_locations(cafile=str(Path(bundle).expanduser()))
    # Some python.org Windows builds do not consistently expose the Windows
    # certificate stores through OpenSSL's default paths. Import those roots
    # explicitly while retaining normal hostname and certificate verification.
    if os.name == "nt" and hasattr(ssl, "enum_certificates"):
        # Python 3.13+ enables OpenSSL's strict RFC 5280 mode by default. Some
        # Windows-managed enterprise roots predate its requirement that CA
        # Basic Constraints be marked critical. Windows trusts those roots;
        # use its compatibility policy while keeping CERT_REQUIRED and
        # hostname verification enabled.
        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict_flag:
            context.verify_flags &= ~strict_flag
        for store in ("ROOT", "CA"):
            try:
                certificates = ssl.enum_certificates(store)
            except OSError:
                continue
            for certificate, encoding, _trust in certificates:
                if encoding == "x509_asn":
                    try:
                        context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
                    except ssl.SSLError:
                        continue
    return context


def tls_failure_hint(error: BaseException) -> str:
    """Add actionable guidance to certificate failures without weakening TLS."""
    text = str(error)
    if "CERTIFICATE_VERIFY_FAILED" not in text and "certificate verify failed" not in text.casefold():
        return text
    return (
        f"{text}. RoadProof kept TLS verification enabled. Install the required CA in the operating-system "
        f"trust store or set {CA_BUNDLE_ENV} to a PEM CA bundle, then run --self-check --network-check"
    )


def check_https_endpoints(*, timeout: int = 15) -> list[tuple[str, str | None]]:
    """Probe representative HTTPS endpoints and return per-service errors."""
    endpoints = {
        "Google Maps": "https://www.google.com/maps",
        # Stable representative Berlin tile; the published metadata endpoint
        # intermittently returns 503 even while the tile service is healthy.
        "Germany basemap.de": "https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/tiles/v2/bm_web_de_3857/15/17604/10746.pbf",
        "Denmark Vejman": "https://geocloud.vd.dk/vejman-stamdata/wfs?service=WFS&request=GetCapabilities",
        "Sweden Trafikverket": "https://geo-netinfo.trafikverket.se/MapService/wms.axd/NetInfo_1_10?SERVICE=WMS&REQUEST=GetCapabilities",
        "Belgium Wegenregister": "https://geo.api.vlaanderen.be/Wegenregister/wfs?service=WFS&request=GetCapabilities",
    }
    context = secure_ssl_context()
    results: list[tuple[str, str | None]] = []
    for name, url in endpoints.items():
        try:
            request = Request(url, headers={"User-Agent": "RoadProof/0.6"})
            with urlopen(request, timeout=timeout, context=context) as response:
                response.read(1)
            results.append((name, None))
        except Exception as exc:  # diagnostic path must report every endpoint
            results.append((name, tls_failure_hint(exc)))
    return results


def _retry_delay(error: BaseException, attempt: int) -> float:
    if isinstance(error, HTTPError):
        value = error.headers.get("Retry-After") if error.headers else None
        if value and value.isdigit():
            return min(30.0, float(value))
    return min(10.0, 1.5 * (attempt + 1)) + random.uniform(0.0, 0.25)


def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    attempts: int = 3,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_per_host: int = 6,
    validate_final_url: Callable[[str], bool] | None = None,
) -> bytes:
    """Fetch one HTTPS resource with bounded retries, size and TLS checks."""
    if not url.startswith("https://"):
        raise NetworkRequestError("Only HTTPS official-service requests are permitted.")
    if _OFFLINE.get() or _OFFLINE_EVENT.is_set():
        raise NetworkRequestError("Offline mode blocked an official-service request because no usable cache entry existed.")
    host = (urlparse(url).hostname or "").casefold()
    if not host or max_per_host < 1:
        raise NetworkRequestError("The HTTPS request host or concurrency limit was invalid.")
    with _HOST_LIMITS_LOCK:
        semaphore = _HOST_LIMITS.setdefault((host, max_per_host), threading.BoundedSemaphore(max_per_host))
    request = Request(url, headers=headers or {})
    last_error: BaseException | None = None
    for attempt in range(attempts):
        started = time.monotonic()
        _metric("request_attempts")
        try:
            with semaphore:
                with urlopen(request, timeout=timeout, context=secure_ssl_context()) as response:
                    if validate_final_url and not validate_final_url(response.geturl()):
                        raise NetworkRequestError("The official service redirected unexpectedly.")
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        raise NetworkRequestError(
                            f"The official service response exceeded the {max_bytes:,}-byte safety limit."
                        )
                    body = response.read(max_bytes + 1)
            _metric("network_seconds", time.monotonic() - started)
            if len(body) > max_bytes:
                raise NetworkRequestError(
                    f"The official service response exceeded the {max_bytes:,}-byte safety limit."
                )
            if not body:
                raise NetworkRequestError("The official service returned an empty response.")
            _metric("downloaded_bytes", len(body))
            return body
        except HTTPError as exc:
            last_error = exc
            retryable = exc.code in TRANSIENT_HTTP_CODES
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            retryable = True
        except (NetworkRequestError, ValueError) as exc:
            last_error = exc
            retryable = False
        _metric("network_seconds", time.monotonic() - started)
        if not retryable or attempt + 1 >= attempts:
            break
        _metric("retries")
        time.sleep(_retry_delay(last_error, attempt))
    _metric("request_failures")
    raise NetworkRequestError(tls_failure_hint(last_error or RuntimeError("unknown network error"))) from last_error


def read_json_cache(path: Path, *, max_age_s: float) -> dict[str, Any] | None:
    try:
        if time.time() - path.stat().st_mtime > max_age_s:
            _metric("cache_misses")
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            _metric("cache_hits")
            return value
        _metric("cache_misses")
        return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _metric("cache_misses")
        return None


def write_json_cache(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{threading.get_ident()}.{time.time_ns()}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
        _metric("cache_writes")
    except OSError:
        _metric("cache_write_failures")
        return


def read_bytes_cache(path: Path, *, max_age_s: float) -> bytes | None:
    try:
        if time.time() - path.stat().st_mtime > max_age_s:
            _metric("cache_misses")
            return None
        value = path.read_bytes()
        if value:
            _metric("cache_hits")
            return value
        _metric("cache_misses")
        return None
    except OSError:
        _metric("cache_misses")
        return None


def write_bytes_cache(path: Path, value: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{threading.get_ident()}.{time.time_ns()}.tmp")
        temporary.write_bytes(value)
        temporary.replace(path)
        _metric("cache_writes")
    except OSError:
        _metric("cache_write_failures")
        return
