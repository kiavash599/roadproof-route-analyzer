"""Local HTTP bridge from the web UI to the canonical RoadProof CLI engine."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

from .google import ALLOWED_INPUT_HOSTS


HOST = "127.0.0.1"
PORT = 8765
ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


class Handler(BaseHTTPRequestHandler):
    server_version = "RoadProofLocalAPI/1.0"

    def _headers(self, status: int, origin: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()

    def _json(self, value: object, status: int = 200) -> None:
        origin = self.headers.get("Origin")
        self._headers(status, origin)
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        if origin not in ALLOWED_ORIGINS:
            self._json({"error": "Origin is not allowed."}, 403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/analyze":
            self._json({"error": "Not found."}, 404)
            return
        origin = self.headers.get("Origin")
        if origin not in ALLOWED_ORIGINS:
            self._json({"error": "Origin is not allowed."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 8_192:
                raise ValueError("Request body must be between 1 and 8192 bytes.")
            body = json.loads(self.rfile.read(length))
            url = str(body.get("url", "")).strip()
            profile = str(body.get("profile", "composition"))
            parsed = urlparse(url)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_INPUT_HOSTS:
                raise ValueError("Provide one HTTPS Google Maps route URL.")
            if profile not in {"composition", "eu-isa"}:
                raise ValueError("Unsupported analysis profile.")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
            return

        try:
            with tempfile.TemporaryDirectory(prefix="roadproof-web-") as output_dir:
                result = subprocess.run(
                    [sys.executable, "-m", "roadproof", "--url", url, "--profile", profile,
                     "--format", "json", "--output-dir", str(Path(output_dir))],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=600, check=False,
                )
            if result.returncode != 0:
                message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Route analysis failed."
                self._json({"error": message}, 422)
                return
            self._json(json.loads(result.stdout))
        except subprocess.TimeoutExpired:
            self._json({"error": "Route analysis exceeded the 10 minute limit."}, 504)
        except (OSError, json.JSONDecodeError) as exc:
            self._json({"error": f"The local analysis engine failed: {exc}"}, 500)

    def log_message(self, format: str, *args: object) -> None:
        print(f"Web API: {format % args}")


def main() -> None:
    print(f"RoadProof analysis API listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
