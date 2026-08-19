"""Command-line entry point for RoadProof."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from . import __version__
from .google import RouteReadError, fetch_google_route
from .report import breakdown_for, load_evidence, markdown_report, render_console, save_report


ROOT = Path(__file__).resolve().parents[1]
RESET = "\x1b[0m"


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
        evidence = load_evidence(ROOT, route["route_fingerprint"])
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


if __name__ == "__main__":
    sys.exit(main())
