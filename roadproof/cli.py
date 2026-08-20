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
from .google import RouteReadError, fetch_google_route
from .report import breakdown_for, load_evidence, markdown_report, render_console, save_report


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
    parser.add_argument("--log-file", type=Path, help="Write a plain-text copy of the complete run output")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--version", action="version", version=f"RoadProof {__version__}")
    return parser


def self_check() -> int:
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
    message(f"Self-check passed. {len(packages)} evidence package(s) available.", "32")
    return 0


def _country_signal(route: dict) -> str:
    countries = sorted({str(value).upper() for value in route.get("countries", []) if value})
    source = str(route.get("country_signal_source") or "source not recorded")
    return f"{', '.join(countries)} ({source})" if countries else source


def _evidence_for_route(route: dict, *, progress) -> tuple[dict | None, str]:
    retained = load_evidence(ROOT, route["route_fingerprint"])
    if retained is not None:
        return retained, str(retained.get("summary") or "Matched retained official-road evidence package")

    countries = {str(value).upper() for value in route.get("countries", []) if value}
    try:
        runtime = analyze_supported_route(route, progress=progress)
    except (DenmarkAdapterError, OfficialAdapterError) as exc:
        return None, f"Official-road adapter failed: {exc}"
    if runtime is not None:
        if not countries and runtime.get("country"):
            route["countries"] = [str(runtime["country"]).upper()]
            route["country_signal_source"] = "official adapter geographic fallback"
        return runtime, str(runtime.get("summary") or "Matched runtime official-road evidence")
    if not countries:
        return None, "Google supplied no country code; no supported runtime adapter was selected safely"
    if len(countries) > 1:
        return None, f"Cross-border country signal ({', '.join(sorted(countries))}); route splitting is required"
    country = next(iter(countries))
    return None, f"No verified runtime evidence was returned for Google country code {country}"


def _run(args: argparse.Namespace) -> int:
    if args.self_check:
        try:
            return self_check()
        except Exception as exc:
            message(f"Self-check failed: {exc}", "31")
            return 1
    if not args.url:
        build_parser().error("--url is required unless --self-check is used")

    if os.name == "nt":
        os.system("")
    message("Resolving the Google Maps link and reading the selected route...", "36")
    try:
        route = fetch_google_route(args.url)
        resolve_country_signal(route)
        evidence, evidence_status = _evidence_for_route(
            route,
            progress=lambda text: message(text, "36"),
        )
        route["country_signal"] = _country_signal(route)
        route["evidence_status"] = evidence_status
        if evidence is None:
            message(evidence_status, "33")
        rows = breakdown_for(route, evidence)
        created_at = datetime.now().astimezone()
        content = markdown_report(route, rows, evidence, args.profile, created_at)
        output_path = save_report(args.output_dir, content, created_at, route)
        render_console(route, rows, evidence, output_path, args.profile)
        return 0
    except RouteReadError as exc:
        message(f"Route analysis stopped: {exc}", "31")
        message("No Markdown report was written because the route evidence was incomplete.", "33")
        return 1
    except KeyboardInterrupt:
        message("\nCancelled.", "33")
        return 130
    except Exception as exc:
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
            message(f"Run log saved: {log_path}", "32")
            return result
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == "__main__":
    sys.exit(main())
