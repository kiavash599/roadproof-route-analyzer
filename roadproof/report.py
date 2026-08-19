"""Evidence matching, console rendering, and Markdown report generation."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import shutil
from typing import Any
import unicodedata

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only before installer runs
    RICH_AVAILABLE = False


CATEGORY_COLORS = {
    "Highway": "bright_blue",
    "Country": "green",
    "City": "magenta",
    "Unresolved": "yellow",
}


def load_evidence(root: Path, fingerprint: str) -> dict[str, Any] | None:
    for path in sorted((root / "evidence").glob("*.json")):
        package = json.loads(path.read_text(encoding="utf-8"))
        if package.get("route_identity", {}).get("route_fingerprint") == fingerprint:
            return package
    return None


def breakdown_for(route: dict[str, Any], evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    if evidence:
        return evidence["breakdown"]
    return [
        {"category": "Highway", "official_layer": "No verified country match", "distance_m": 0.0, "status": "Unresolved"},
        {"category": "Country", "official_layer": "No verified country match", "distance_m": 0.0, "status": "Unresolved"},
        {"category": "City", "official_layer": "No verified country match", "distance_m": 0.0, "status": "Unresolved"},
        {
            "category": "Unresolved",
            "official_layer": "Exact Google distance; road class not assigned",
            "distance_m": float(route["distance_m"]),
            "status": "Unresolved",
        },
    ]


def duration_text(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def _status_markup(status: str) -> str:
    return status


ANSI = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "blue": "\x1b[94m",
    "cyan": "\x1b[96m",
    "green": "\x1b[92m",
    "magenta": "\x1b[95m",
    "yellow": "\x1b[93m",
    "red": "\x1b[91m",
    "gray": "\x1b[90m",
    "white": "\x1b[97m",
}


def color(text: str, name: str, *, bold: bool = False) -> str:
    prefix = ANSI["bold"] if bold else ""
    return f"{prefix}{ANSI[name]}{text}{ANSI['reset']}"


def _plain_len(text: str) -> int:
    return len(re.sub(r"\x1b\[[0-9;]*m", "", text))


def _pad(text: str, width: int, *, right: bool = False) -> str:
    missing = max(0, width - _plain_len(text))
    return (" " * missing + text) if right else (text + " " * missing)


def print_table(title: str, headers: list[str], rows: list[list[str]], right_columns: set[int] | None = None) -> None:
    right_columns = right_columns or set()
    terminal_width = max(90, shutil.get_terminal_size((120, 30)).columns)
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], _plain_len(value))
    total = sum(widths) + 3 * len(widths) + 1
    if total > terminal_width:
        flexible = 1 if len(widths) > 2 else 0
        widths[flexible] = max(24, widths[flexible] - (total - terminal_width))

    print(color(title, "white", bold=True))
    print("┌" + "┬".join("─" * (width + 2) for width in widths) + "┐")
    header_cells = [f" {_pad(color(value, 'cyan', bold=True), widths[i])} " for i, value in enumerate(headers)]
    print("│" + "│".join(header_cells) + "│")
    print("├" + "┼".join("─" * (width + 2) for width in widths) + "┤")
    for row in rows:
        cells = []
        for i, value in enumerate(row):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", value)
            if len(plain) > widths[i]:
                clipped_plain = plain[: max(1, widths[i] - 1)] + "…"
                value = clipped_plain
            cells.append(f" {_pad(value, widths[i], right=i in right_columns)} ")
        print("│" + "│".join(cells) + "│")
    print("└" + "┴".join("─" * (width + 2) for width in widths) + "┘")


def _render_ansi_console(
    route: dict[str, Any],
    rows: list[dict[str, Any]],
    evidence: dict[str, Any] | None,
    output_path: Path,
    profile: str,
) -> None:
    evidence_text = str(evidence.get("summary") or "Matched verified official-road evidence package") if evidence else "No matching road-network evidence package"
    print("\n" + color("  ROADPROOF  ", "white", bold=True) + color(" Evidence-first route analysis", "blue", bold=True))
    print_table(
        "Route summary",
        ["Field", "Value"],
        [
            ["Route", route["route_name"]],
            ["Exact distance", f"{route['distance_m'] / 1000:.3f} km"],
            ["Google duration", duration_text(route["duration_s"])],
            ["Legs / maneuvers", f"{len(route['legs'])} / {route['maneuver_count']}"],
            ["Evidence", color(evidence_text, "green" if evidence else "yellow")],
            ["Profile", "EU ISA 2021/1958" if profile == "eu-isa" else "Composition only"],
        ],
    )

    total = route["distance_m"]
    composition_rows = []
    for row in rows:
        category = row["category"]
        distance_m = float(row["distance_m"])
        status_color = "cyan" if "inferred" in row["status"].lower() else "green" if "confirmed" in row["status"].lower() else "yellow"
        composition_rows.append(
            [
                color(category, {"bright_blue": "blue", "green": "green", "magenta": "magenta", "yellow": "yellow"}[CATEGORY_COLORS[category]], bold=True),
                row["official_layer"],
                f"{distance_m / 1000:.3f} km",
                f"{100 * distance_m / total:.2f}%",
                color(_status_markup(row["status"]), status_color),
            ]
        )
    print_table(
        "Road-type composition",
        ["Category", "Official / evidence layer", "Distance", "Share", "Evidence status"],
        composition_rows,
        {2, 3},
    )

    if profile == "eu-isa":
        target_rows = [[
            "Overall distance",
            f"{total / 1000:.3f} km",
            "400.000 km",
            color("Meets", "green") if total >= 400_000 else color(f"Short by {(400_000-total)/1000:.3f} km", "red"),
        ]]
        for category in ("Highway", "Country", "City"):
            distance_m = next(float(row["distance_m"]) for row in rows if row["category"] == category)
            target_rows.append([
                category,
                f"{distance_m / 1000:.3f} km",
                "100.000 km",
                color("Meets", "green") if distance_m >= 100_000 else color(f"Short by {(100_000-distance_m)/1000:.3f} km", "red"),
            ])
        target_rows.append(["Darkness", "Not measured", "60.000 km / 15%", color("Unresolved", "yellow")])
        print_table("EU ISA profile check", ["Check", "Measured", "Target", "Result"], target_rows, {1, 2})

    print("\n" + color("Markdown report saved:", "green", bold=True) + f" {output_path}")


def _rich_status(value: str) -> Text:
    lowered = value.lower()
    style = "bold green" if "confirmed" in lowered or value == "Meets" else "bold yellow"
    if "short by" in lowered:
        style = "bold red"
    if "inferred" in lowered:
        style = "bold cyan"
    return Text(value, style=style)


def _render_rich_console(
    route: dict[str, Any],
    rows: list[dict[str, Any]],
    evidence: dict[str, Any] | None,
    output_path: Path,
    profile: str,
) -> None:
    console = Console(highlight=False, soft_wrap=False)
    console.print()
    console.print(
        Panel.fit(
            "[bold white]ROADPROOF[/bold white]  [bold bright_blue]Evidence-first route analysis[/bold bright_blue]",
            border_style="bright_blue",
            padding=(0, 2),
        )
    )

    summary = Table(title="Route summary", box=box.ROUNDED, header_style="bold cyan", show_lines=True)
    summary.add_column("Field", style="bold white", no_wrap=True)
    summary.add_column("Value", overflow="fold")
    summary.add_row("Route", str(route["route_name"]))
    if route.get("origin_name") and route.get("destination_name"):
        summary.add_row("Endpoints", f"{route['origin_name']} -> {route['destination_name']}")
    summary.add_row("Exact distance", f"{route['distance_m'] / 1000:.3f} km")
    summary.add_row("Google duration", duration_text(route["duration_s"]))
    summary.add_row("Legs / maneuvers", f"{len(route['legs'])} / {route['maneuver_count']}")
    evidence_text = Text(
        str(evidence.get("summary") or "Matched verified official-road evidence package"),
        style="bold green",
    ) if evidence else Text("No matching road-network evidence package", style="bold yellow")
    summary.add_row("Evidence", evidence_text)
    summary.add_row("Profile", "EU ISA 2021/1958" if profile == "eu-isa" else "Composition only")
    summary.add_row("Fingerprint", route["route_fingerprint"][:16] + "...")
    console.print(summary)

    composition = Table(title="Road-type composition", box=box.ROUNDED, header_style="bold cyan", show_lines=True)
    composition.add_column("Category", no_wrap=True)
    composition.add_column("Official / evidence layer", overflow="fold", max_width=54)
    composition.add_column("Distance", justify="right", no_wrap=True)
    composition.add_column("Share", justify="right", no_wrap=True)
    composition.add_column("Evidence status", overflow="fold")
    category_styles = {
        "Highway": "bold bright_blue",
        "Country": "bold green",
        "City": "bold magenta",
        "Unresolved": "bold yellow",
    }
    total = float(route["distance_m"])
    for row in rows:
        distance_m = float(row["distance_m"])
        composition.add_row(
            Text(row["category"], style=category_styles[row["category"]]),
            str(row["official_layer"]),
            f"{distance_m / 1000:.3f} km",
            f"{100 * distance_m / total:.2f}%",
            _rich_status(str(row["status"])),
        )
    console.print(composition)

    if profile == "eu-isa":
        targets = Table(title="EU ISA profile check", box=box.ROUNDED, header_style="bold cyan", show_lines=True)
        targets.add_column("Check", style="bold white")
        targets.add_column("Measured", justify="right", no_wrap=True)
        targets.add_column("Target", justify="right", no_wrap=True)
        targets.add_column("Result", overflow="fold")
        overall_result = "Meets" if total >= 400_000 else f"Short by {(400_000-total)/1000:.3f} km"
        targets.add_row("Overall distance", f"{total / 1000:.3f} km", "400.000 km", _rich_status(overall_result))
        for category in ("Highway", "Country", "City"):
            distance_m = next(float(row["distance_m"]) for row in rows if row["category"] == category)
            result = "Meets" if distance_m >= 100_000 else f"Short by {(100_000-distance_m)/1000:.3f} km"
            targets.add_row(category, f"{distance_m / 1000:.3f} km", "100.000 km", _rich_status(result))
        targets.add_row("Darkness", "Not measured", "60.000 km / 15%", _rich_status("Unresolved"))
        console.print(targets)

    console.print(
        Panel(
            Text.assemble(
                ("Markdown report saved\n", "bold green"),
                (str(output_path), "white"),
            ),
            border_style="green",
            expand=False,
        )
    )


def render_console(
    route: dict[str, Any],
    rows: list[dict[str, Any]],
    evidence: dict[str, Any] | None,
    output_path: Path,
    profile: str,
) -> None:
    if RICH_AVAILABLE:
        _render_rich_console(route, rows, evidence, output_path, profile)
    else:
        _render_ansi_console(route, rows, evidence, output_path, profile)


def _markdown_table(rows: list[dict[str, Any]], total_m: float) -> str:
    lines = [
        "| Road type | Official / evidence layer | Distance | Share | Status |",
        "|---|---|---:|---:|---|",
    ]
    for row in rows:
        distance_m = float(row["distance_m"])
        lines.append(
            f"| {_markdown_cell(row['category'])} | {_markdown_cell(row['official_layer'])} | "
            f"{distance_m / 1000:.3f} km | {100 * distance_m / total_m:.2f}% | "
            f"**{_markdown_cell(row['status'])}** |"
        )
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def markdown_report(
    route: dict[str, Any],
    rows: list[dict[str, Any]],
    evidence: dict[str, Any] | None,
    profile: str,
    created_at: datetime,
) -> str:
    total_m = float(route["distance_m"])
    matched = evidence is not None
    lines = [
        "# RoadProof route analysis",
        "",
        f"Analysis date: {created_at.astimezone().isoformat(timespec='seconds')}",
        "",
        f"Input route: <{route['input_url']}>",
        f"Resolved route: <{route['resolved_url']}>",
        "",
        "## Outcome",
        "",
        f"The Google-selected route is **{total_m / 1000:.3f} km** with "
        f"a Google duration of **{duration_text(route['duration_s'])}**, "
        f"**{route['maneuver_count']} maneuvers** across **{len(route['legs'])} legs**. "
        f"The Google route total reconciles with the maneuver sum.",
        "",
        "| Route field | Value |",
        "|---|---|",
        f"| Route name | {_markdown_cell(route['route_name'])} |",
        f"| Origin | {_markdown_cell(route.get('origin_name') or 'Not exposed by the Google URL')} |",
        f"| Destination | {_markdown_cell(route.get('destination_name') or 'Not exposed by the Google URL')} |",
        f"| Exact distance | {total_m / 1000:.3f} km |",
        f"| Google duration | {duration_text(route['duration_s'])} |",
        f"| Legs / maneuvers | {len(route['legs'])} / {route['maneuver_count']} |",
        f"| Maneuver-distance sum | {route['maneuver_sum_m'] / 1000:.3f} km |",
        "",
        "### Leg summary",
        "",
        "| Leg | Distance | Google duration |",
        "|---:|---:|---:|",
    ]
    for leg in route["legs"]:
        lines.append(
            f"| {leg['leg']} | {leg['distance_m'] / 1000:.3f} km | {duration_text(leg['duration_s'])} |"
        )
    lines.append("")
    if matched:
        if evidence.get("runtime"):
            evidence_paragraph = (
                "RoadProof matched this route at runtime against official Danish road and zone data. "
                "The exact-distance allocation below calibrates accepted official paths to Google's "
                "maneuver distances. The named official attributes are matched on accepted segments; "
                "mapping them to EU buckets and allocating distance remain inferred, while failed paths "
                "remain unresolved."
            )
        else:
            evidence_paragraph = (
                "The route fingerprint matches a retained, verified Denmark evidence package. "
                "The exact-distance allocation below is inferred by calibrating matched official-road "
                "segments to Google's exact maneuver distances. Official attributes are confirmed on "
                "the matched segments; the allocation itself remains inferred."
            )
        lines.extend(
            [
                evidence_paragraph,
                "",
            ]
        )
    else:
        lines.extend(
            [
                "No retained official-road evidence package has the same geometry fingerprint. "
                "RoadProof therefore reports the exact Google distance but does not invent a Highway, "
                "Country, or City allocation.",
                "",
            ]
        )
    lines.extend([_markdown_table(rows, total_m), ""])

    if profile == "eu-isa":
        lines.extend(["## EU ISA 2021/1958 profile", ""])
        overall_gap = max(0.0, 400_000 - total_m)
        lines.append(
            f"Overall distance: **{total_m / 1000:.3f} km**; "
            + ("meets the 400 km target." if not overall_gap else f"short by **{overall_gap / 1000:.3f} km**.")
        )
        lines.extend(["", "| Category | Measured | 100 km target |", "|---|---:|---|"])
        for category in ("Highway", "Country", "City"):
            distance_m = next(float(row["distance_m"]) for row in rows if row["category"] == category)
            result = "Meets" if distance_m >= 100_000 else f"Short by {(100_000-distance_m)/1000:.3f} km"
            lines.append(f"| {category} | {distance_m / 1000:.3f} km | {result} |")
        lines.extend(
            [
                "| Darkness | Not measured | Unresolved (target: 60 km / 15%) |",
                "",
                "Darkness cannot be determined from route geometry. It depends on the actual driving time.",
                "",
            ]
        )

    lines.extend(
        [
            "## Confirmed, inferred, unresolved",
            "",
            "### Confirmed",
            "",
            f"- Google returned an exact route total of {total_m / 1000:.3f} km.",
            f"- {route['maneuver_count']} maneuver distances sum to the same total.",
            f"- Google response SHA-256: `{route['response_sha256']}`.",
            f"- Maneuver-level route fingerprint: `{route['route_fingerprint']}`.",
            "",
            "### Inferred",
            "",
        ]
    )
    if matched:
        lines.extend(f"- {item}" for item in evidence.get("inferred", []))
    else:
        lines.append("- None. No road-type allocation was inferred without a matching evidence package.")
    lines.extend(["", "### Unresolved", ""])
    if matched:
        lines.extend(f"- {item}" for item in evidence.get("unresolved", []))
    else:
        lines.append("- The country road-network adapter has not verified this exact route geometry.")
    lines.extend(
        [
            "- Darkness distance is not available from a planned route.",
            "",
            "## Evidence and method",
            "",
            "The route was not recreated from its waypoints in another router. RoadProof read the "
            "Google Maps directions payload selected by the shared link and reconciled its exact total "
            "against its maneuver distances.",
            "",
        ]
    )
    if matched:
        lines.extend([evidence["method"], ""])
        lines.extend(["Official data snapshots:", ""])
        lines.extend(f"- {item}" for item in evidence.get("official_sources", []))
        lines.append("")
    lines.extend(
        [
            "## Reproducibility",
            "",
            f"- Route fingerprint: `{route['route_fingerprint']}`",
            f"- Directions response hash: `{route['response_sha256']}`",
            f"- Profile: `{profile}`",
            "- The generated report and console table use the same computed data structure.",
            "",
        ]
    )
    return "\n".join(lines)


WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def _safe_filename_component(value: str, *, max_length: int = 76) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = [char if char.isalnum() or char in {"-", "_"} else "-" for char in normalized]
    safe = re.sub(r"-+", "-", "".join(characters)).strip("-_. ")
    safe = safe[:max_length].rstrip("-_. ")
    if not safe:
        safe = "google-route"
    if safe in WINDOWS_RESERVED_NAMES:
        safe = f"route-{safe}"
    return safe


def report_filename(route: dict[str, Any], created_at: datetime) -> str:
    origin = route.get("origin_name")
    destination = route.get("destination_name")
    if origin and destination:
        if str(origin).strip().casefold() == str(destination).strip().casefold():
            label = f"{origin}-loop"
        else:
            label = f"{origin}-to-{destination}"
    else:
        label = str(route.get("route_name") or "google-route")
    safe_label = _safe_filename_component(label)
    safe_stamp = created_at.strftime("%Y%m%d-%H%M%S")
    safe_hash = re.sub(r"[^a-f0-9]", "", str(route["route_fingerprint"]).lower())[:10]
    return f"roadproof-{safe_label}-{safe_stamp}-{safe_hash}.md"


def save_report(output_dir: Path, content: str, created_at: datetime, route: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = output_dir / report_filename(route, created_at)
    candidate = requested
    collision = 2
    while True:
        try:
            with candidate.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            return candidate.resolve()
        except FileExistsError:
            candidate = requested.with_name(f"{requested.stem}-{collision}{requested.suffix}")
            collision += 1
