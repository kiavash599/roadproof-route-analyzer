from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from roadproof.google import (
    RouteReadError,
    _validate_redirect_url,
    normalize_google_maps_url,
    parse_directions_response,
    route_endpoints_from_url,
    route_fingerprint,
)
from roadproof.report import breakdown_for, load_evidence, markdown_report, report_filename, save_report


def sample_response() -> bytes:
    def step(distance, duration, lat, lon, text):
        core = [None, text, [distance, f"{distance} m"], [duration, "1 min"], None, None, None,
                [None, None, [None, None, lat, lon]]]
        return [core]

    steps = [
        step(400, 40, 55.0, 12.0, "Head north"),
        step(600, 60, 55.001, 12.001, "Turn right"),
    ]
    leg_header = [None, None, [1000, "1 km", 0], [100, "2 min"]]
    destination = [None, None, [None, None, [None, None, 55.002, 12.002]]]
    leg = [leg_header, [[None, steps]], None, None, destination]
    route = [[None, "Sample route", [1000, "1 km", 0], [100, "2 min"]], [leg]]
    return json.dumps([[None, [route]]]).encode("utf-8")


class RoadProofTests(unittest.TestCase):
    def test_google_response_reconciles_and_fingerprints(self):
        route = parse_directions_response(
            sample_response(),
            input_url="https://maps.app.goo.gl/example",
            resolved_url="https://www.google.com/maps/dir/example",
        )
        self.assertEqual(route["distance_m"], 1000)
        self.assertEqual(route["maneuver_sum_m"], 1000)
        self.assertEqual(route["maneuver_count"], 2)
        self.assertEqual(route["route_fingerprint"], route_fingerprint(1000, route["maneuvers"]))

    def test_pasted_short_and_full_google_maps_urls_are_normalized(self):
        short = normalize_google_maps_url('  <https://maps.app.goo.gl/Example123>  ')
        self.assertEqual(short, "https://maps.app.goo.gl/Example123")

        full = normalize_google_maps_url(
            "Google Maps route: https://www.google.com/maps/dir/Copenhagen/Roskilde/data=!4m2!4m1"
        )
        self.assertEqual(
            full,
            "https://www.google.com/maps/dir/Copenhagen/Roskilde/data=!4m2!4m1",
        )
        localized = normalize_google_maps_url("https://www.google.dk/maps/dir/Aarhus/Odense")
        self.assertEqual(localized, "https://www.google.dk/maps/dir/Aarhus/Odense")

    def test_non_google_and_non_maps_urls_are_rejected(self):
        for value in (
            "https://example.com/maps/dir/a/b",
            "http://www.google.com/maps/dir/a/b",
            "https://www.google.com/search?q=maps",
            "not a URL",
        ):
            with self.subTest(value=value), self.assertRaises(RouteReadError):
                normalize_google_maps_url(value)

    def test_redirects_are_checked_before_following(self):
        _validate_redirect_url("https://www.google.com/maps/dir/A/B")
        _validate_redirect_url("https://consent.google.com/m?continue=https%3A%2F%2Fwww.google.com")
        with self.assertRaises(RouteReadError):
            _validate_redirect_url("https://example.com/collect")

    def test_route_endpoints_are_read_from_path_or_api_query(self):
        self.assertEqual(
            route_endpoints_from_url(
                "https://www.google.com/maps/dir/Copenhagen%20Central/Roskilde/data=!4m2"
            ),
            ("Copenhagen Central", "Roskilde"),
        )
        self.assertEqual(
            route_endpoints_from_url(
                "https://www.google.com/maps/dir/?api=1&origin=K%C3%B8benhavn&destination=Odense"
            ),
            ("København", "Odense"),
        )

    def test_unknown_route_is_not_silently_classified(self):
        route = parse_directions_response(
            sample_response(),
            input_url="https://maps.app.goo.gl/example",
            resolved_url="https://www.google.com/maps/dir/example",
        )
        rows = breakdown_for(route, None)
        self.assertEqual(sum(float(row["distance_m"]) for row in rows), 1000)
        self.assertEqual(next(row for row in rows if row["category"] == "Highway")["distance_m"], 0)
        self.assertEqual(next(row for row in rows if row["category"] == "Unresolved")["distance_m"], 1000)

    def test_markdown_and_saved_file_use_same_values(self):
        route = parse_directions_response(
            sample_response(),
            input_url="https://maps.app.goo.gl/example",
            resolved_url="https://www.google.com/maps/dir/example",
        )
        rows = breakdown_for(route, None)
        created = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
        content = markdown_report(route, rows, None, "eu-isa", created)
        self.assertIn("1.000 km", content)
        self.assertIn("No retained official-road evidence package", content)
        self.assertIn("### Leg summary", content)
        self.assertIn("Resolved route:", content)
        with tempfile.TemporaryDirectory() as directory:
            path = save_report(Path(directory), content, created, route)
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_report_filename_is_meaningful_safe_and_unique(self):
        route = parse_directions_response(
            sample_response(),
            input_url="https://maps.app.goo.gl/example",
            resolved_url="https://www.google.com/maps/dir/Copenhagen%20Central/Roskilde/data=!4m2",
        )
        created = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
        filename = report_filename(route, created)
        self.assertTrue(filename.startswith("roadproof-copenhagen-central-to-roskilde-20260819-080000-"))
        self.assertTrue(filename.endswith(".md"))
        loop_route = {**route, "destination_name": route["origin_name"]}
        self.assertIn("copenhagen-central-loop", report_filename(loop_route, created))
        with tempfile.TemporaryDirectory() as directory:
            first = save_report(Path(directory), "first", created, route)
            second = save_report(Path(directory), "second", created, route)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(encoding="utf-8"), "first")
            self.assertEqual(second.read_text(encoding="utf-8"), "second")

    def test_all_evidence_packages_reconcile_to_their_route_total(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "evidence").glob("*.json"):
            package = json.loads(path.read_text(encoding="utf-8"))
            total = sum(float(row["distance_m"]) for row in package["breakdown"])
            self.assertAlmostEqual(total, float(package["route_identity"]["distance_m"]), places=2)
            self.assertIsNotNone(load_evidence(root, package["route_identity"]["route_fingerprint"]))

    def test_cross_platform_launcher_files_are_present(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("installer.ps1", "start.ps1", "start.bat", "installer.sh", "start.sh"):
            self.assertTrue((root / name).is_file(), name)
        if os.name != "nt":
            self.assertTrue(os.access(root / "installer.sh", os.X_OK))
            self.assertTrue(os.access(root / "start.sh", os.X_OK))
        if shutil.which("bash"):
            for name in ("installer.sh", "start.sh"):
                completed = subprocess.run(
                    ["bash", "-n", str(root / name)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
