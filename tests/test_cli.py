from __future__ import annotations

from datetime import datetime, timezone
from http.client import RemoteDisconnected
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from roadproof.adapters import analyze_supported_route, resolve_country_signal
from roadproof.cli import _evidence_for_route, main
from roadproof.crossborder import analyze_cross_border_track
from roadproof.denmark import (
    DenmarkAdapterError,
    _haversine,
    analyze_denmark_route,
    classify_official_properties,
)
from roadproof.belgium import analyze_belgium_route, classify_flanders_properties
from roadproof.germany import GermanyPointClassifier, analyze_germany_route, classify_basemap_properties
from roadproof.google import (
    LEGACY_ROUTE_FINGERPRINT_VERSION,
    ROUTE_FINGERPRINT_VERSION,
    RouteReadError,
    _open_with_retry,
    _validate_redirect_url,
    normalize_google_maps_url,
    parse_directions_response,
    route_endpoints_from_url,
    route_fingerprint,
)
from roadproof.geometry import reconstruct_maneuver_geometries
from roadproof.network import (
    NetworkRequestError,
    fetch_bytes,
    offline_requests,
    performance_metrics,
    read_json_cache,
    reset_performance_metrics,
    secure_ssl_context,
    write_json_cache,
)
from roadproof.profiles import evaluate_profile
from roadproof.report import breakdown_for, load_evidence, markdown_report, report_filename, save_report
from roadproof.sampled import OfficialAdapterError, PointEvidence, analyze_sampled_route
from roadproof.sweden import analyze_sweden_route
from roadproof.track import TrackReadError, read_track, reconcile_track, track_distance_m


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
    route = [[None, "Sample route", [1000, "1 km", 0], [100, "2 min"]], [leg], *([None] * 9), ["DK"]]
    return json.dumps([[None, [route]]]).encode("utf-8")


class RoadProofTests(unittest.TestCase):
    def test_osrm_reconstruction_accepts_only_distance_reconciled_legs(self):
        route = {
            "maneuvers": [
                {"distance_m": 1_000.0, "start_lat": 52.5, "start_lon": 13.4, "end_lat": 52.5, "end_lon": 13.41},
                {"distance_m": 500.0, "start_lat": 52.5, "start_lon": 13.41, "end_lat": 52.5, "end_lon": 13.42},
            ]
        }
        response = {
            "routes": [{"legs": [
                {"distance": 1_050.0, "steps": [{"geometry": {"coordinates": [[13.4, 52.5], [13.405, 52.501], [13.41, 52.5]]}}]},
                {"distance": 900.0, "steps": [{"geometry": {"coordinates": [[13.41, 52.5], [13.42, 52.5]]}}]},
            ]}]
        }
        with tempfile.TemporaryDirectory() as cache_dir, patch(
            "roadproof.geometry.fetch_bytes", return_value=json.dumps(response).encode("utf-8")
        ):
            geometries, status = reconstruct_maneuver_geometries(route, cache_dir=Path(cache_dir))
        self.assertEqual(set(geometries), {0})
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["accepted"], 1)
        self.assertEqual(status["rejected"][0]["sequence"], 1)

    def test_reconciled_osrm_maneuver_geometry_replaces_only_that_bad_chord(self):
        route = {
            "countries": ["DE"], "distance_m": 1000.0, "maneuver_count": 1,
            "route_fingerprint": "route", "automatic_geometry_status": {"accepted": 1},
            "_reconstructed_maneuver_points": {0: [(52.5, 13.4), (52.501, 13.405), (52.5, 13.41)]},
            "maneuvers": [{"sequence": 0, "distance_m": 1000.0, "start_lat": 52.5, "start_lon": 13.4, "end_lat": 52.5, "end_lon": 13.4}],
        }
        package = analyze_sampled_route(
            route, country="DE", country_name="German", adapter="test",
            classifier=lambda _lat, _lon: PointEvidence("City", "test"),
            official_layers={category: "test" for category in ("Highway", "Country", "City", "Unresolved")},
            official_sources=["test"], mapping_note="test",
        )
        self.assertEqual(package["analysis_basis"], "hybrid_osrm_reconstructed_maneuvers")
        self.assertEqual(package["matched_maneuvers"], 1)
        self.assertAlmostEqual(next(row["distance_m"] for row in package["breakdown"] if row["category"] == "City"), 1000.0)

    def test_single_country_adapter_prefers_reconciled_exact_track_over_bad_maneuver_chord(self):
        points = [(52.5000, 13.4000), (52.5005, 13.4050), (52.5010, 13.4100)]
        track_total = track_distance_m(points)
        route = {
            "countries": ["DE"],
            "distance_m": track_total,
            "maneuver_count": 1,
            "route_fingerprint": "route",
            "geometry_hash": "track-hash",
            "track_reconciliation": {"status": "consistent_not_equivalent"},
            "_exact_track_points": points,
            "maneuvers": [
                {
                    "sequence": 0,
                    "distance_m": track_total,
                    "start_lat": 52.5,
                    "start_lon": 13.4,
                    "end_lat": 52.5,
                    "end_lon": 13.4,
                }
            ],
        }
        package = analyze_sampled_route(
            route,
            country="DE",
            country_name="German",
            adapter="test",
            classifier=lambda _lat, _lon: PointEvidence("City", "test"),
            official_layers={category: "test" for category in ("Highway", "Country", "City", "Unresolved")},
            official_sources=["test"],
            mapping_note="test",
        )
        self.assertEqual(package["analysis_basis"], "reconciled_imported_exact_track")
        self.assertEqual(package["route_identity"]["geometry_hash"], "track-hash")
        self.assertEqual(package["matched_track_edges"], 2)
        self.assertEqual(package["matched_maneuvers"], 0)
        rows = {row["category"]: row for row in package["breakdown"]}
        self.assertAlmostEqual(rows["City"]["distance_m"], track_total)
        self.assertEqual(rows["Unresolved"]["distance_m"], 0)

    def test_single_country_adapter_ignores_conflicting_track(self):
        route = {
            "countries": ["DE"],
            "distance_m": 1_000.0,
            "maneuver_count": 1,
            "route_fingerprint": "route",
            "geometry_hash": "track-hash",
            "track_reconciliation": {"status": "conflict"},
            "_exact_track_points": [(52.5, 13.4), (52.501, 13.401)],
            "maneuvers": [
                {
                    "sequence": 0,
                    "distance_m": 1_000.0,
                    "start_lat": 52.5,
                    "start_lon": 13.4,
                    "end_lat": 52.5,
                    "end_lon": 13.4,
                }
            ],
        }
        with self.assertRaisesRegex(OfficialAdapterError, "No maneuver passed"):
            analyze_sampled_route(
                route,
                country="DE",
                country_name="German",
                adapter="test",
                classifier=lambda _lat, _lon: PointEvidence("City", "test"),
                official_layers={category: "test" for category in ("Highway", "Country", "City", "Unresolved")},
                official_sources=["test"],
                mapping_note="test",
            )

    def test_cross_border_track_keeps_transition_unresolved_and_reconciles_total(self):
        points = [(50.8, 4.4), (50.81, 4.41), (52.5, 13.4), (52.51, 13.41)]
        total = track_distance_m(points)
        route = {
            "countries": ["BE", "DE"],
            "_exact_track_points": points,
            "track_reconciliation": {"status": "consistent_not_equivalent"},
            "geometry_hash": "geometry",
            "route_fingerprint": "route",
            "distance_m": total,
            "maneuver_count": 3,
        }

        def analyzer(subroute, *, progress=None):
            del progress
            category = "City" if subroute["countries"] == ["BE"] else "Highway"
            distance = subroute["distance_m"]
            return {
                "adapter": f"fake-{subroute['countries'][0]}",
                "breakdown": [
                    {
                        "category": item,
                        "distance_m": distance if item == category else 0.0,
                        "official_layer": "test",
                        "status": "test",
                    }
                    for item in ("Highway", "Country", "City", "Unresolved")
                ],
                "matched_maneuvers": len(subroute["maneuvers"]),
                "matched_distance_m": distance,
                "official_sources": ["test"],
            }

        def boundary_contains(country, point):
            latitude, longitude = point
            return (country == "BE" and longitude < 5.0) or (country == "DE" and longitude > 10.0)

        package = analyze_cross_border_track(
            route, analyzer=analyzer, boundary_contains=boundary_contains
        )
        self.assertIsNotNone(package)
        rows = {row["category"]: row for row in package["breakdown"]}
        self.assertAlmostEqual(sum(row["distance_m"] for row in rows.values()), total)
        self.assertGreater(rows["Unresolved"]["distance_m"], 0)
        self.assertGreater(rows["Highway"]["distance_m"], 0)
        self.assertGreater(rows["City"]["distance_m"], 0)
        self.assertEqual({item["country"] for item in package["country_results"]}, {"BE", "DE"})

    def test_cross_border_requires_every_sample_to_stay_inside_one_official_boundary(self):
        points = [(50.0, 4.0), (50.0, 8.0)]
        route = {
            "countries": ["BE", "DE"],
            "_exact_track_points": points,
            "track_reconciliation": {"status": "consistent_not_equivalent"},
            "geometry_hash": "geometry",
            "route_fingerprint": "route",
            "distance_m": track_distance_m(points),
            "maneuver_count": 1,
        }

        with self.assertRaises(OfficialAdapterError):
            analyze_cross_border_track(
                route,
                analyzer=lambda _route, **_kwargs: self.fail("ambiguous edge must not dispatch"),
                boundary_contains=lambda country, point: (
                    country == "BE" and point[1] <= 5.0
                ) or (country == "DE" and point[1] >= 7.0),
            )

    def test_cli_dispatches_reconciled_multi_country_track_to_cross_border_engine(self):
        route = {
            "route_fingerprint": "cross-border",
            "legacy_route_fingerprint": None,
            "countries": ["BE", "DE"],
            "_exact_track_points": [(50.8, 4.4), (52.5, 13.4)],
            "track_reconciliation": {"status": "consistent_not_equivalent"},
        }
        package = {
            "summary": "cross-border result",
            "country": "MULTI",
            "performance": {},
            "breakdown": [
                {"category": category, "distance_m": 1 if category == "Unresolved" else 0}
                for category in ("Highway", "Country", "City", "Unresolved")
            ],
        }
        with (
            patch("roadproof.cli.load_evidence", return_value=None),
            patch("roadproof.cli.analyze_cross_border_track", return_value=package) as cross_border,
            patch("roadproof.cli.analyze_supported_route") as single_country,
        ):
            evidence, status = _evidence_for_route(route, progress=lambda _text: None)
        self.assertIs(evidence, package)
        self.assertEqual(status, "cross-border result")
        self.assertEqual(route["result_status"], "partial")
        cross_border.assert_called_once()
        single_country.assert_not_called()

    def test_python_track_hash_matches_web_canonical_contract(self):
        gpx = """<gpx><trk><trkseg><trkpt lat="55" lon="12"/><trkpt lat="55.001" lon="12.002"/></trkseg></trk></gpx>"""
        geojson = '{"type":"LineString","coordinates":[[12,55],[12.002,55.001]]}'
        with tempfile.TemporaryDirectory() as directory:
            gpx_path = Path(directory) / "track.gpx"
            json_path = Path(directory) / "track.geojson"
            gpx_path.write_text(gpx, encoding="utf-8")
            json_path.write_text(geojson, encoding="utf-8")
            gpx_track = read_track(gpx_path)
            json_track = read_track(json_path)
        self.assertEqual(gpx_track["geometry_hash"], json_track["geometry_hash"])
        self.assertEqual(gpx_track["geometry_hash_version"], "roadproof-track-geometry-v1")
        self.assertEqual(gpx_track["point_count"], 2)

    def test_track_reconciliation_never_claims_equivalence(self):
        geojson = '{"type":"LineString","coordinates":[[12,55],[12.002,55.002]]}'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.geojson"
            path.write_text(geojson, encoding="utf-8")
            track = read_track(path)
        route = {
            "distance_m": track["distance_m"],
            "maneuvers": [
                {"start_lat": 55, "start_lon": 12, "end_lat": 55.002, "end_lon": 12.002}
            ],
        }
        reconciliation = reconcile_track(route, track)
        self.assertEqual(reconciliation["status"], "consistent_not_equivalent")
        self.assertFalse(reconciliation["equivalence_claimed"])

    def test_track_xml_entities_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.gpx"
            path.write_text('<!DOCTYPE gpx [<!ENTITY x "1">]><gpx/>', encoding="utf-8")
            with self.assertRaisesRegex(TrackReadError, "not permitted"):
                read_track(path)

    def test_profile_distinguishes_failure_from_insufficient_evidence(self):
        rows = [
            {"category": "Highway", "distance_m": 50_000},
            {"category": "Country", "distance_m": 100_000},
            {"category": "City", "distance_m": 100_000},
            {"category": "Unresolved", "distance_m": 150_000},
        ]
        result = evaluate_profile("eu-isa", 400_000, rows)
        checks = {check["check"]: check for check in result["checks"]}
        self.assertEqual(result["schema"], "roadproof.profile-result.v1")
        self.assertEqual(result["version"], "2023-09-21")
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(checks["overall_distance"]["state"], "meets")
        self.assertEqual(checks["highway"]["state"], "insufficient_evidence")
        self.assertEqual(checks["darkness"]["state"], "not_measured")

    def test_cache_performance_metrics_record_hits_misses_and_writes(self):
        reset_performance_metrics()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            self.assertIsNone(read_json_cache(path, max_age_s=60))
            write_json_cache(path, {"features": []})
            self.assertIsNotNone(read_json_cache(path, max_age_s=60))
        metrics = performance_metrics()
        self.assertEqual(metrics["cache_misses"], 1)
        self.assertEqual(metrics["cache_writes"], 1)
        self.assertEqual(metrics["cache_hits"], 1)

    def test_versioned_route_identity_keeps_legacy_alias(self):
        route = parse_directions_response(
            sample_response(),
            input_url="https://maps.app.goo.gl/example",
            resolved_url="https://www.google.com/maps/dir/example",
        )
        self.assertEqual(route["route_fingerprint_version"], ROUTE_FINGERPRINT_VERSION)
        self.assertEqual(route["legacy_route_fingerprint_version"], LEGACY_ROUTE_FINGERPRINT_VERSION)
        self.assertNotEqual(route["route_fingerprint"], route["legacy_route_fingerprint"])
        self.assertIsNone(route["geometry_hash"])

    def test_retained_evidence_can_match_legacy_fingerprint(self):
        route = {
            "route_fingerprint": "new-fingerprint",
            "legacy_route_fingerprint": "legacy-fingerprint",
            "countries": ["DK"],
        }
        retained = {
            "summary": "legacy match",
            "breakdown": [{"category": "Unresolved", "distance_m": 0}],
        }
        with patch(
            "roadproof.cli.load_evidence",
            side_effect=lambda _root, fingerprint: retained if fingerprint == "legacy-fingerprint" else None,
        ):
            evidence, status = _evidence_for_route(route, progress=lambda _text: None)
        self.assertIs(evidence, retained)
        self.assertEqual(status, "legacy match")
        self.assertEqual(route["matched_evidence_fingerprint"], "legacy-fingerprint")

    def test_offline_mode_blocks_cache_miss_before_network(self):
        with (
            offline_requests(),
            patch("roadproof.network.urlopen") as opener,
            self.assertRaisesRegex(NetworkRequestError, "Offline mode blocked"),
        ):
            fetch_bytes("https://example.test/data")
        opener.assert_not_called()

    def test_offline_mode_propagates_to_worker_threads(self):
        with offline_requests(), patch("roadproof.network.urlopen") as opener:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_bytes, "https://example.test/data")
                with self.assertRaisesRegex(NetworkRequestError, "Offline mode blocked"):
                    future.result()
        opener.assert_not_called()

    def test_adapter_listing_has_machine_readable_schema(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["--list-adapters", "--format", "json"])
        self.assertEqual(result, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(document["schema"], "roadproof.diagnostic.v1")
        self.assertEqual({item["country"] for item in document["adapters"]}, {"BE", "DE", "DK", "SE"})

    def test_json_format_writes_versioned_result_and_clean_stdout(self):
        route = parse_directions_response(
            sample_response(),
            input_url="https://maps.app.goo.gl/example",
            resolved_url="https://www.google.com/maps/dir/example",
        )
        route["country_signal_source"] = "Google payload"
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            errors = io.StringIO()
            log_path = Path(directory) / "run.log"
            track_path = Path(directory) / "track.geojson"
            track_path.write_text(
                '{"type":"LineString","coordinates":[[12,55],[12.002,55.002]]}',
                encoding="utf-8",
            )
            with (
                patch("roadproof.cli.fetch_google_route", return_value=route),
                patch("roadproof.cli.analyze_supported_route", return_value=None),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                result = main(
                    [
                        "--url",
                        "https://maps.app.goo.gl/example",
                        "--format",
                        "json",
                        "--output-dir",
                        directory,
                        "--log-file",
                        str(log_path),
                        "--track",
                        str(track_path),
                    ]
                )
            self.assertEqual(result, 0)
            document = json.loads(output.getvalue())
            self.assertEqual(document["schema"], "roadproof.result.v1")
            self.assertEqual(document["status"], "unsupported")
            self.assertEqual(document["route"]["distance_m"], 1000)
            self.assertEqual(document["route"]["exact_track"]["format"], "GeoJSON")
            self.assertEqual(document["route"]["track_reconciliation"]["status"], "conflict")
            self.assertEqual(sum(row["distance_m"] for row in document["evidence"]["breakdown"]), 1000)
            self.assertEqual(document["profile"]["schema"], "roadproof.profile-result.v1")
            self.assertIn("request_attempts", document["performance"])
            saved = Path(document["outputs"]["json"])
            self.assertTrue(saved.is_file())
            self.assertEqual(json.loads(saved.read_text(encoding="utf-8")), document)
            self.assertIn("Run log saved", errors.getvalue())
            self.assertTrue(log_path.is_file())

    def test_shared_json_cache_recovers_from_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertIsNone(read_json_cache(path, max_age_s=60))
            write_json_cache(path, {"features": []})
            self.assertEqual(read_json_cache(path, max_age_s=60), {"features": []})

    def test_shared_fetch_rejects_oversized_response(self):
        class Response:
            headers = {"Content-Length": "100"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://example.test/data"

        with (
            patch("roadproof.network.urlopen", return_value=Response()),
            self.assertRaisesRegex(RuntimeError, "safety limit"),
        ):
            fetch_bytes("https://example.test/data", max_bytes=10)

    def test_shared_tls_context_keeps_verification_enabled(self):
        context = secure_ssl_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_sampled_allocation_reconciles_to_route_total_without_negative_distance(self):
        route = {
            "countries": ["DE"],
            "distance_m": 1000,
            "route_fingerprint": "reconcile-test",
            "maneuver_count": 1,
            "maneuvers": [
                {
                    "sequence": 0,
                    "distance_m": 1001,
                    "start_lat": 52.5,
                    "start_lon": 13.4,
                    "end_lat": 52.509,
                    "end_lon": 13.4,
                }
            ],
        }
        package = analyze_sampled_route(
            route,
            country="DE",
            country_name="German",
            adapter="test",
            classifier=lambda _lat, _lon: PointEvidence("City", "test"),
            official_layers={category: category for category in ("Highway", "Country", "City", "Unresolved")},
            official_sources=[],
            mapping_note="test",
        )
        self.assertIsNotNone(package)
        rows = package["breakdown"]
        self.assertAlmostEqual(sum(float(row["distance_m"]) for row in rows), 1000)
        self.assertTrue(all(float(row["distance_m"]) >= 0 for row in rows))
        self.assertAlmostEqual(package["matched_distance_m"], 1000)
        self.assertEqual(package["performance"]["unique_sample_points"], 3)
        self.assertEqual(package["performance"]["service_error_count"], 0)

    def test_germany_classifier_checks_adjacent_tile_near_boundary(self):
        classifier = GermanyPointClassifier()
        requested = []

        def tile(tile_x, tile_y):
            requested.append((tile_x, tile_y))
            features = []
            if tile_x == 101:
                features = [
                    {
                        "properties": {"klasse": "Bundesstrasse"},
                        "geometry": {"type": "LineString", "coordinates": [[0, 1000], [0, 3000]]},
                    }
                ]
            return {
                "Verkehrslinie": {"extent": 4096, "features": features},
                "Siedlungsflaeche": {"extent": 4096, "features": []},
            }

        classifier._tile = tile
        with patch("roadproof.germany._tile_coordinates", return_value=(100, 200, 0.999, 0.5)):
            result = classifier(52.5, 13.4)
        self.assertEqual(result.category, "Country")
        self.assertIn((101, 200), requested)

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
        self.assertEqual(route["countries"], ["DK"])

    def test_denmark_official_fields_are_not_reduced_to_speed_alone(self):
        self.assertEqual(
            classify_official_properties({"VEJTYPESKILTET": "Motorvej"})[0],
            "Highway",
        )
        self.assertEqual(
            classify_official_properties({"HAST_GENEREL_HAST": "50 - Indenfor byzonetavler"})[0],
            "City",
        )
        self.assertEqual(
            classify_official_properties({"HAST_GENEREL_HAST": "80 - Udenfor byzonetavler"})[0],
            "Country",
        )
        self.assertEqual(
            classify_official_properties({"HAST_GAELDENDE_HAST": 130})[0],
            "Unresolved",
        )

    def test_runtime_denmark_adapter_matches_new_route_without_saved_fingerprint(self):
        first_start = (55.0, 12.0)
        turn = (55.0, 12.01)
        destination = (55.01, 12.01)
        first_distance = round(_haversine(first_start, turn))
        second_distance = round(_haversine(turn, destination))
        route = {
            "countries": ["DK"],
            "distance_m": first_distance + second_distance,
            "maneuver_count": 2,
            "route_fingerprint": "not-a-retained-package",
            "maneuvers": [
                {
                    "sequence": 0,
                    "distance_m": first_distance,
                    "start_lat": first_start[0],
                    "start_lon": first_start[1],
                    "end_lat": turn[0],
                    "end_lon": turn[1],
                },
                {
                    "sequence": 1,
                    "distance_m": second_distance,
                    "start_lat": turn[0],
                    "start_lon": turn[1],
                    "end_lat": destination[0],
                    "end_lon": destination[1],
                },
            ],
        }
        official = {
            "type": "FeatureCollection",
            "timeStamp": "2026-08-19T12:00:00Z",
            "features": [
                {
                    "id": "motorway-1",
                    "type": "Feature",
                    "properties": {"VEJTYPESKILTET": "Motorvej", "KODE_VEJTYPESKILTET": "1"},
                    "geometry": {"type": "LineString", "coordinates": [[12.0, 55.0], [12.01, 55.0]]},
                },
                {
                    "id": "city-1",
                    "type": "Feature",
                    "properties": {"VEJTYPESKILTET": "Øvrige veje", "KODE_VEJTYPESKILTET": "9"},
                    "geometry": {"type": "LineString", "coordinates": [[12.01, 55.0], [12.01, 55.01]]},
                },
            ],
        }
        zones = {
            "type": "FeatureCollection",
            "timeStamp": "2026-08-19T12:00:00Z",
            "features": [
                {
                    "id": "zone-1",
                    "type": "Feature",
                    "properties": {"zone": 1, "zonestatus": "Byzone"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [12.005, 54.999],
                            [12.015, 54.999],
                            [12.015, 55.011],
                            [12.005, 55.011],
                            [12.005, 54.999],
                        ]],
                    },
                }
            ],
        }
        evidence = analyze_denmark_route(
            route,
            fetch_box=lambda _box, _cache: official,
            fetch_zones=lambda _box, _cache: zones,
        )
        self.assertIsNotNone(evidence)
        rows = {row["category"]: row for row in evidence["breakdown"]}
        self.assertAlmostEqual(rows["Highway"]["distance_m"], first_distance, places=3)
        self.assertAlmostEqual(rows["City"]["distance_m"], second_distance, places=3)
        self.assertAlmostEqual(rows["Unresolved"]["distance_m"], 0, places=3)
        self.assertTrue(evidence["runtime"])

        def unavailable_zones(_box, _cache):
            raise DenmarkAdapterError("temporary zone service failure")

        vejman_only = analyze_denmark_route(
            route,
            fetch_box=lambda _box, _cache: official,
            fetch_zones=unavailable_zones,
        )
        self.assertIsNotNone(vejman_only)
        vejman_rows = {row["category"]: row for row in vejman_only["breakdown"]}
        self.assertAlmostEqual(vejman_rows["Highway"]["distance_m"], first_distance, places=3)
        self.assertAlmostEqual(vejman_rows["Unresolved"]["distance_m"], second_distance, places=3)
        self.assertTrue(any("Plandata zone fallback was unavailable" in item for item in vejman_only["unresolved"]))

    def test_denmark_adapter_does_not_claim_another_country(self):
        route = {
            "countries": ["SE"],
            "maneuvers": [{
                "start_lat": 55.6,
                "start_lon": 12.5,
                "end_lat": 55.7,
                "end_lon": 12.6,
            }],
        }
        self.assertIsNone(
            analyze_denmark_route(
                route,
                fetch_box=lambda _box, _cache: self.fail("Vejman must not be queried"),
                fetch_zones=lambda _box, _cache: self.fail("Plandata must not be queried"),
            )
        )

    def test_new_country_adapters_require_unanimous_official_samples(self):
        start = (52.5, 13.3)
        end = (52.5, 13.31)
        distance = round(_haversine(start, end))
        base_route = {
            "distance_m": distance,
            "maneuver_count": 1,
            "route_fingerprint": "synthetic-country-adapter",
            "maneuvers": [{
                "sequence": 0,
                "distance_m": distance,
                "start_lat": start[0],
                "start_lon": start[1],
                "end_lat": end[0],
                "end_lon": end[1],
            }],
        }
        cases = (
            ("DE", analyze_germany_route, "City"),
            ("SE", analyze_sweden_route, "Highway"),
            ("BE", analyze_belgium_route, "Country"),
        )
        for country, analyzer, expected in cases:
            with self.subTest(country=country):
                evidence = analyzer(
                    {**base_route, "countries": [country]},
                    classifier=lambda _lat, _lon, category=expected: PointEvidence(category, "test official layer"),
                )
                self.assertIsNotNone(evidence)
                rows = {row["category"]: row for row in evidence["breakdown"]}
                self.assertAlmostEqual(rows[expected]["distance_m"], distance)
                self.assertAlmostEqual(rows["Unresolved"]["distance_m"], 0)
                self.assertEqual(evidence["country"], country)

    def test_new_country_field_mappings_are_explicit(self):
        self.assertEqual(
            classify_basemap_properties({"klasse": "Bundesautobahn"}, in_settlement=False).category,
            "Highway",
        )
        self.assertEqual(
            classify_basemap_properties({"klasse": "Gemeindestraße"}, in_settlement=True).category,
            "City",
        )
        self.assertEqual(
            classify_flanders_properties({"morfologischeWegklasse": "autosnelweg"}, built_up=False).category,
            "Highway",
        )
        self.assertEqual(
            classify_flanders_properties(
                {"morfologischeWegklasse": "weg bestaande uit één rijbaan"},
                built_up=True,
            ).category,
            "City",
        )

    def test_registry_does_not_claim_unsupported_or_cross_border_routes(self):
        self.assertIsNone(analyze_supported_route({"countries": ["FR"], "maneuvers": []}))
        self.assertIsNone(analyze_supported_route({"countries": ["DE", "BE"], "maneuvers": []}))

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

    def test_google_transport_disconnect_is_retried(self):
        sentinel = object()

        class FlakyOpener:
            def __init__(self):
                self.calls = 0

            def open(self, _request, *, timeout):
                self.calls += 1
                self.assert_timeout = timeout
                if self.calls == 1:
                    raise RemoteDisconnected("remote closed")
                return sentinel

        opener = FlakyOpener()
        with patch("roadproof.google.time.sleep") as sleep:
            response = _open_with_retry(
                opener,
                Request("https://maps.app.goo.gl/example"),
                timeout=17,
            )
        self.assertIs(response, sentinel)
        self.assertEqual(opener.calls, 2)
        self.assertEqual(opener.assert_timeout, 17)
        sleep.assert_called_once_with(1)

    def test_permanent_google_http_error_is_not_retried(self):
        class PermanentFailureOpener:
            def __init__(self):
                self.calls = 0

            def open(self, request, *, timeout):
                self.calls += 1
                raise HTTPError(request.full_url, 404, "not found", {}, None)

        opener = PermanentFailureOpener()
        with (
            patch("roadproof.google.time.sleep") as sleep,
            self.assertRaises(HTTPError),
        ):
            _open_with_retry(
                opener,
                Request("https://maps.app.goo.gl/example"),
                timeout=17,
            )
        self.assertEqual(opener.calls, 1)
        sleep.assert_not_called()

    def test_google_retry_exhaustion_reports_attempt_count(self):
        class DisconnectedOpener:
            def __init__(self):
                self.calls = 0

            def open(self, _request, *, timeout):
                self.calls += 1
                raise RemoteDisconnected("remote closed")

        opener = DisconnectedOpener()
        with (
            patch("roadproof.google.time.sleep") as sleep,
            self.assertRaisesRegex(URLError, "failed after 3 attempts"),
        ):
            _open_with_retry(
                opener,
                Request("https://maps.app.goo.gl/example"),
                timeout=17,
            )
        self.assertEqual(opener.calls, 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (2,)])

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
        self.assertIn("No matching road-network evidence package", content)
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
        if os.name != "nt" and shutil.which("bash"):
            for name in ("installer.sh", "start.sh"):
                completed = subprocess.run(
                    ["bash", "-n", str(root / name)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_installers_require_official_numpy_binary_and_verify_runtime(self):
        root = Path(__file__).resolve().parents[1]
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        windows_installer = (root / "installer.ps1").read_text(encoding="utf-8")
        unix_installer = (root / "installer.sh").read_text(encoding="utf-8")
        windows_launcher = (root / "start.ps1").read_text(encoding="utf-8")
        unix_launcher = (root / "start.sh").read_text(encoding="utf-8")
        self.assertIn('numpy==2.2.6; python_version < "3.14"', requirements)
        self.assertIn('numpy==2.3.5; python_version >= "3.14"', requirements)
        for installer in (windows_installer, unix_installer):
            self.assertIn("--only-binary=:all:", installer)
            self.assertIn("mapbox_vector_tile", installer)
            self.assertIn("Numpy built with MINGW-W64", installer)
        for launcher in (windows_launcher, unix_launcher):
            self.assertIn("importlib.metadata", launcher)
            self.assertIn("incompatible NumPy environment", launcher)

    def test_missing_google_country_signal_is_reported_instead_of_hidden(self):
        route = {
            "route_fingerprint": "missing-country",
            "countries": [],
        }
        with (
            patch("roadproof.cli.load_evidence", return_value=None),
            patch("roadproof.cli.analyze_supported_route", return_value=None),
        ):
            evidence, status = _evidence_for_route(route, progress=lambda _text: None)
        self.assertIsNone(evidence)
        self.assertIn("Google supplied no country code", status)

    def test_missing_google_country_signal_uses_unique_coordinate_fallback(self):
        route = {
            "countries": [],
            "origin_name": "52.5187846, 13.4026534",
            "destination_name": "Liebknechtbrücke 1, Berlin, Germany",
            "maneuvers": [
                {
                    "start_lat": 52.5187846,
                    "start_lon": 13.4026534,
                    "end_lat": 52.399293,
                    "end_lon": 13.4102326,
                }
            ],
        }
        self.assertEqual(resolve_country_signal(route), ["DE"])
        self.assertEqual(route["countries"], ["DE"])
        self.assertEqual(route["country_signal_source"], "URL country label + coordinate guard")

    def test_coordinate_fallback_does_not_guess_in_overlapping_border_envelopes(self):
        route = {
            "countries": [],
            "origin_name": "50.75, 6.20",
            "destination_name": "50.80, 6.30",
            "maneuvers": [
                {
                    "start_lat": 50.75,
                    "start_lon": 6.20,
                    "end_lat": 50.80,
                    "end_lon": 6.30,
                }
            ],
        }
        self.assertEqual(resolve_country_signal(route), [])
        self.assertIn("ambiguous", route["country_signal_source"])

    def test_adapter_failure_reason_is_preserved(self):
        route = {
            "route_fingerprint": "adapter-error",
            "countries": ["DE"],
        }
        with (
            patch("roadproof.cli.load_evidence", return_value=None),
            patch(
                "roadproof.cli.analyze_supported_route",
                side_effect=OfficialAdapterError("basemap.de test failure"),
            ),
        ):
            evidence, status = _evidence_for_route(route, progress=lambda _text: None)
        self.assertIsNone(evidence)
        self.assertEqual(status, "Official-road adapter failed: basemap.de test failure")

    def test_self_check_can_write_plain_persistent_run_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            self.assertEqual(main(["--self-check", "--log-file", str(path)]), 0)
            content = path.read_text(encoding="utf-8")
            self.assertIn("Self-check passed", content)
            self.assertIn("Run log saved", content)
            self.assertNotIn("\x1b[", content)


if __name__ == "__main__":
    unittest.main()
    performance_metrics,
    reset_performance_metrics,
