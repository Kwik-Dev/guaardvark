#!/usr/bin/env python3
"""Dev CORS origins must follow the configured ports.

An install that relocates its dev server (so it can run beside a sibling) had
its websockets refused: engineio logged the frontend's own origin as unlisted
because the allowlist named 5173/5175/3000 literally.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.app import dev_allowed_origins  # noqa: E402

FRONTEND = "http://localhost:5173"


class TestDevAllowedOrigins(unittest.TestCase):

    def test_a_relocated_vite_port_is_allowed(self):
        with mock.patch.dict(os.environ, {"VITE_PORT": "5273"}):
            origins = dev_allowed_origins(FRONTEND)
        self.assertIn("http://localhost:5273", origins)
        self.assertIn("http://127.0.0.1:5273", origins)

    def test_a_relocated_flask_port_is_allowed(self):
        with mock.patch.dict(os.environ, {"FLASK_PORT": "5100"}):
            origins = dev_allowed_origins(FRONTEND)
        self.assertIn("http://localhost:5100", origins)
        self.assertIn("http://127.0.0.1:5100", origins)

    def test_the_stock_ports_survive_a_relocation(self):
        # A customised port is additive; a default install must not lose 5173.
        with mock.patch.dict(os.environ, {"VITE_PORT": "5273"}):
            origins = dev_allowed_origins(FRONTEND)
        for port in ("3000", "5173", "5175"):
            self.assertIn(f"http://localhost:{port}", origins)

    def test_defaults_when_nothing_is_set(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ["GUAARDVARK_MODE"] = "test"
            origins = dev_allowed_origins(FRONTEND)
        self.assertIn("http://localhost:5173", origins)
        self.assertIn("http://localhost:5000", origins)

    def test_the_frontend_url_is_always_carried(self):
        with mock.patch.dict(os.environ, {"VITE_PORT": "4321"}):
            origins = dev_allowed_origins("http://example.test:9999")
        self.assertIn("http://example.test:9999", origins)

    def test_no_duplicates(self):
        # VITE_PORT=5173 on a default install must not double the entry.
        with mock.patch.dict(os.environ, {"VITE_PORT": "5173"}):
            origins = dev_allowed_origins(FRONTEND)
        deduped = [o for o in origins if o != FRONTEND]
        self.assertEqual(len(deduped), len(set(deduped)))


if __name__ == "__main__":
    unittest.main()
