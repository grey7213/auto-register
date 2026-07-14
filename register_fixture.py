#!/usr/bin/env python3
"""Minimal local endpoint for exercising register_account.py."""

from __future__ import annotations

import json
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer


class RegistrationHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/register":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size))
        if not payload.get("email") or not payload.get("password"):
            self.send_error(400, "email and password are required")
            return
        response = json.dumps({"created": True, "email": payload["email"]}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_: object) -> None:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18080)
    arguments = parser.parse_args()
    HTTPServer(("127.0.0.1", arguments.port), RegistrationHandler).serve_forever()
