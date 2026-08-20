"""Command-line entry point for RoadProof."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import TextIO

from . import __version__
from .adapters import DenmarkAdapterError, OfficialAdapterError, analyze_supported_route, resolve_country_signal
from .diagnostics import adapter_document, cache_document
from .crossborder import analyze_cross_border_track
from .google import RouteReadError, fetch_google_route
from .geometry import reconstruct_maneuver_geometries
from .network import (
    check_https_endpoints,
    offline_requests,
    performance_metrics,
    reset_performance_metrics,
)
from .report import breakdown_for, load_evidence, markdown_report, render_console, save_report
from .results import result_document, save_json_result
from .track import TrackReadError, read_track, reconcile_track


ROOT = Path(__file__).resolve().parents[1]
RESET = "\x1b[0m"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class PlainLogTee:
    """Mirror a terminal stream to a plain UTF-8 diagnostic log."""

    def __init__(self, terminal: TextIO, log: TextIO):
        self.terminal = terminal
        self.log = log

    def write(self, value: str) -> int:
        written = self.terminal.write(value)
        self.log.write(ANSI_RE.sub("", value))
        return len(value) if written is None else written

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()

    def isatty(self) -> bool:
        return self.terminal.isatty()

    def __getattr__(self, name: str):
        return getattr(self.terminal, name)


def message(text: str, color: str = "36") -> None:
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        print(f"\x1b[{color}m{text}{RESET}")
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roadproof",
        description="Evidence-first Google Maps route composition analysis",
    )
    parser.add_argument("--url", help="Google Maps route link")
    parser.add_argument(
        "--profile",
        choices=("composition", "eu-isa"),
        default="eu-isa",
        help="Report profile (default: eu-isa)",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--track", type=Path, help="Exact GPX, GeoJSON, or KML track to identify separately")
    parser.add_argument("--no-auto-geometry", action="store_true", help="Disable OSRM geometry reconstruction")
    parser.add_argument(
        "--format",
        choices=("console", "markdown", "json", "all"),
        default="console",
        help="Output mode; console preserves the traditional console + Markdown behavior",
    )
    parser.add_argument("--log-file", type=Path, help="Write a plain-text copy of the complete run output")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--diagnose-network", action="store_true", help="Check supported live public services")
    parser.add_argument("--list-adapters", action="store_true", help="List installed official-data adapters")
    parser.add_argument("--inspect-cache", action="store_true", help="Inspect cached official data without modifying it")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Resolve Google normally but prohibit official-service requests; use retained/cached evidence only",
    )
    parser.add_argument(
        "--network-check",
        action="store_true",
        help="With --self-check, verify TLS connectivity to supported public services",
    )
    parser.add_argument(
        "--strict-evidence",
        action="store_true",
        help="Return exit code 2 after writing the report unless all route distance is classified",
    )
    parser.add_argument("--version", action="version", version=f"RoadProof {__version__}")
    return parser


def self_check(*, network_check: bool = False) -> int:
    packages = list((ROOT / "evidence").glob("*.json"))
    if not packages:
        raise RuntimeError("No evidence packages were installed.")
    for path in packages:
        package = json.loads(path.read_text(encoding="utf-8"))
        required = {"route_identity", "breakdown", "method"}
        missing = required - package.keys()
        if missing:
            raise RuntimeError(f"{path.name} is missing: {', '.join(sorted(missing))}")
        route_total = float(package["route_identity"]["distance_m"])
        breakdown_total = sum(float(row["distance_m"]) for row in package["breakdown"])
        if abs(route_total - breakdown_total) > 0.01:
            raise RuntimeError(
                f"{path.name} breakdown is {breakdown_total:.3f} m but route identity is {route_total:.3f} m."
            )
    if network_check:
        failures = []
        for service, error in check_https_endpoints():
            if error:
                failures.append(f"{service}: {error}")
                message(f"Network check failed — {service}: {error}", "31")
            else:
                message(f"Network check passed — {service}", "32")
        if failures:
            raise RuntimeError(f"{len(failures)} supported service connectivity check(s) failed.")
    message(f"Self-check passed. {len(packages)} evidence package(s) available.", "32")
    return 0


def _country_signal(route: dict) -> str:
    countries = sorted({str(value).upper() for value in route.get("countries", []) if value})
    source = str(route.get("country_signal_source") or "source not recorded")
    return f"{', '.join(countries)} ({source})" if countries else source


def _evidence_for_route(route: dict, *, progress) -> tuple[dict | None, str]:
    reset_performance_metrics()
    retained = None
    retained_identity = None
    for fingerprint in (route["route_fingerprint"], route.get("legacy_route_fingerprint")):
        if not fingerprint:
            continue
        retained = load_evidence(ROOT, fingerprint)
        if retained is not None:
            retained_identity = fingerprint
            break
    if retained is not None:
        unresolved = sum(
            float(row["distance_m"])
            for row in retained.get("breakdown", [])
            if row.get("category") == "Unresolved"
        )
        route["result_status"] = "complete" if unresolved <= 0.01 else "partial"
        route["matched_evidence_fingerprint"] = retained_identity
        route["performance"] = {"evidence_source": "retained", **performance_metrics()}
        return retained, str(retained.get("summary") or "Matched retained official-road evidence package")

    countries = {str(value).upper() for value in route.get("countries", []) if value}
    try:
        runtime = (
            analyze_cross_border_track(route, progress=progress)
            if len(countries) > 1 and route.get("_exact_track_points")
            else analyze_supported_route(route, progress=progress)
        )
    except (DenmarkAdapterError, OfficialAdapterError) as exc:
        route["performance"] = {"evidence_source": "runtime", **performance_metrics()}
        route["result_status"] = "service_unavailable"
        return None, f"Official-road adapter failed: {exc}"
    if runtime is not None:
        runtime["performance"] = {**runtime.get("performance", {}), **performance_metrics()}
        route["performance"] = runtime["performance"]
        if not countries and runtime.get("country"):
            route["countries"] = [str(runtime["country"]).upper()]
            route["country_signal_source"] = "official adapter geographic fallback"
        unresolved = sum(
            float(row["distance_m"])
            for row in runtime.get("breakdown", [])
            if row.get("category") == "Unresolved"
        )
        route["result_status"] = "complete" if unresolved <= 0.01 else "partial"
        return runtime, str(runtime.get("summary") or "Matched runtime official-road evidence")
    if not countries:
        route["performance"] = {"evidence_source": "runtime", **performance_metrics()}
        route["result_status"] = "unsupported"
        return None, "Google supplied no country code; no supported runtime adapter was selected safely"
    if len(countries) > 1:
        route["performance"] = {"evidence_source": "runtime", **performance_metrics()}
        route["result_status"] = "cross_border_unsupported"
        return None, f"Cross-border country signal ({', '.join(sorted(countries))}); route splitting is required"
    country = next(iter(countries))
    route["performance"] = {"evidence_source": "runtime", **performance_metrics()}
    route["result_status"] = "unsupported"
    return None, f"No verified runtime evidence was returned for Google country code {country}"


def _run(args: argparse.Namespace) -> int:
    if args.list_adapters:
        document = adapter_document()
        if args.format == "json":
            print(json.dumps(document, ensure_ascii=False, indent=2))
        else:
            for adapter in document["adapters"]:
                message(
                    f"{adapter['country']}  {adapter['country_name']}  {adapter['adapter_version']}  "
                    f"{adapter['official_source']}",
                    "36",
                )
        return 0
    if args.inspect_cache:
        document = cache_document()
        if args.format == "json":
            print(json.dumps(document, ensure_ascii=False, indent=2))
        else:
            message(f"Cache root: {document['root']}", "36")
            message(
                f"Files: {document['file_count']}; size: {document['total_bytes']:,} bytes; "
                f"stale: {document['stale_count']}; corrupt: {document['corrupt_count']}",
                "32" if document["corrupt_count"] == 0 else "33",
            )
        return 0 if document["corrupt_count"] == 0 else 1
    if args.diagnose_network:
        results = check_https_endpoints()
        document = {
            "schema": "roadproof.diagnostic.v1",
            "kind": "network",
            "services": [
                {"service": service, "status": "available" if error is None else "unavailable", "error": error}
                for service, error in results
            ],
        }
        if args.format == "json":
            print(json.dumps(document, ensure_ascii=False, indent=2))
        else:
            for item in document["services"]:
                message(
                    f"{item['service']}: {item['status']}" + (f" — {item['error']}" if item["error"] else ""),
                    "32" if item["error"] is None else "31",
                )
        return 0 if all(error is None for _, error in results) else 1
    if args.self_check:
        try:
            return self_check(network_check=args.network_check)
        except Exception as exc:
            message(f"Self-check failed: {exc}", "31")
            return 1
    if not args.url:
        build_parser().error("--url is required unless --self-check is used")

    if os.name == "nt":
        os.system("")
    if args.format != "json":
        message("Resolving the Google Maps link and reading the selected route...", "36")
    try:
        route = fetch_google_route(args.url)
        resolve_country_signal(route)
        if args.track is not None:
            track = read_track(args.track.expanduser().resolve())
            route["geometry_hash"] = track["geometry_hash"]
            route["geometry_hash_version"] = track["geometry_hash_version"]
            route["geometry_hash_status"] = "imported_exact_track"
            route["exact_track"] = {key: value for key, value in track.items() if key != "points"}
            route["_exact_track_points"] = track["points"]
            route["track_reconciliation"] = reconcile_track(route, track)
        elif not args.no_auto_geometry and not args.offline and route.get("countries"):
            if args.format != "json":
                message("Reconstructing detailed geometry through Google maneuver anchors...", "36")
            candidates, candidate_status = reconstruct_maneuver_geometries(route)
            route["automatic_geometry_status"] = candidate_status
            route["_reconstructed_maneuver_points"] = candidates
        with offline_requests(args.offline):
            evidence, evidence_status = _evidence_for_route(
                route,
                progress=(lambda _text: None) if args.format == "json" else (lambda text: message(text, "36")),
            )
        route["country_signal"] = _country_signal(route)
        route["evidence_status"] = evidence_status
        if evidence is None and args.format != "json":
            message(evidence_status, "33")
        rows = breakdown_for(route, evidence)
        created_at = datetime.now().astimezone()
        output_files: dict[str, str] = {}
        markdown_path = None
        if args.format in {"console", "markdown", "all"}:
            content = markdown_report(route, rows, evidence, args.profile, created_at)
            markdown_path = save_report(args.output_dir, content, created_at, route)
            output_files["markdown"] = str(markdown_path)
        document = result_document(
            route,
            rows,
            evidence,
            args.profile,
            created_at,
            output_files=output_files,
        )
        if args.format in {"json", "all"}:
            json_path = save_json_result(args.output_dir, document, created_at, route)
            output_files["json"] = str(json_path)
        if args.format in {"console", "all"}:
            if markdown_path is None:  # defensive; console always saves Markdown
                raise RuntimeError("Console output requires a Markdown report path.")
            render_console(route, rows, evidence, markdown_path, args.profile)
            if args.format == "all":
                message(f"JSON result saved: {output_files['json']}", "32")
        elif args.format == "markdown":
            message(f"Markdown report saved: {output_files['markdown']}", "32")
        else:
            print(json.dumps(document, ensure_ascii=False, indent=2))
        if args.strict_evidence and route.get("result_status") != "complete":
            if args.format == "json":
                print(f"Strict evidence check failed: {route.get('result_status', 'unknown')}", file=sys.stderr)
            else:
                message(f"Strict evidence check failed: {route.get('result_status', 'unknown')}", "31")
            return 2
        return 0
    except (RouteReadError, TrackReadError) as exc:
        if args.format == "json":
            print(f"Route analysis stopped: {exc}", file=sys.stderr)
            print("No JSON result was written because the route evidence was incomplete.", file=sys.stderr)
        else:
            message(f"Route analysis stopped: {exc}", "31")
            message("No Markdown report was written because the route evidence was incomplete.", "33")
        return 1
    except KeyboardInterrupt:
        message("\nCancelled.", "33")
        return 130
    except Exception as exc:
        if args.format == "json":
            print(f"Unexpected error: {exc}", file=sys.stderr)
        else:
            message(f"Unexpected error: {exc}", "31")
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.log_file is None:
        return _run(args)

    log_path = args.log_file.expanduser().resolve()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w", encoding="utf-8", newline="\n")
    except OSError as exc:
        message(f"Could not create the run log ({exc}); continuing without it.", "33")
        return _run(args)

    with log:
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = PlainLogTee(original_stdout, log)
        sys.stderr = PlainLogTee(original_stderr, log)
        try:
            result = _run(args)
            if args.format == "json":
                print(f"Run log saved: {log_path}", file=sys.stderr)
            else:
                message(f"Run log saved: {log_path}", "32")
            return result
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == "__main__":
    sys.exit(main())
