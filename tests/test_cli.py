from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from roadproof.google import parse_directions_response, route_fingerprint
from roadproof.report import breakdown_for, load_evidence, markdown_report, save_report


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
        with tempfile.TemporaryDirectory() as directory:
            path = save_report(Path(directory), content, created, route["route_fingerprint"])
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_all_evidence_packages_reconcile_to_their_route_total(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "evidence").glob("*.json"):
            package = json.loads(path.read_text(encoding="utf-8"))
            total = sum(float(row["distance_m"]) for row in package["breakdown"])
            self.assertAlmostEqual(total, float(package["route_identity"]["distance_m"]), places=2)
            self.assertIsNotNone(load_evidence(root, package["route_identity"]["route_fingerprint"]))


if __name__ == "__main__":
    unittest.main()
