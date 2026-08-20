"""Opt-in live checks; run on schedule, never as deterministic unit tests."""

from __future__ import annotations

import os
import unittest

from roadproof.germany import GermanyPointClassifier
from roadproof.network import check_https_endpoints


@unittest.skipUnless(os.environ.get("ROADPROOF_LIVE_TESTS") == "1", "set ROADPROOF_LIVE_TESTS=1")
class LiveServiceTests(unittest.TestCase):
    def test_supported_service_tls_and_availability(self):
        failures = {service: error for service, error in check_https_endpoints(timeout=30) if error}
        self.assertEqual(failures, {})

    def test_germany_representative_tile_decodes(self):
        evidence = GermanyPointClassifier()(52.5200, 13.4050)
        self.assertIn(evidence.category, {"Highway", "Country", "City", "Unresolved"})
        self.assertTrue(evidence.layer)


if __name__ == "__main__":
    unittest.main()
