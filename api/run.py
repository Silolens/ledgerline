"""Vercel Python serverless function: GET /api/run.

Runs the deterministic engine demo once and returns its JSON serialization.
The response is a pure function of the code: ``run_demo()`` reads no wall clock
and no randomness, so every request returns byte-identical output. There is no
input, no state, and no mutation — the endpoint cannot change anything.

Security note: this route is intentionally UNAUTHENTICATED. That is safe here
only because it is a read-only, side-effect-free replay of a fixed scenario with
no secrets and no user data in scope. Any future endpoint that accepts input or
touches real state must not copy this pattern without adding auth.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# src-layout: the package lives under <repo>/src, which is not on the path when
# Vercel imports this file from the repo root. Add it before importing.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from ledgerline.demo import run_demo  # noqa: E402  (path set up above)
from ledgerline.web.serialize import demo_to_dict  # noqa: E402


def _payload() -> bytes:
    return json.dumps(demo_to_dict(run_demo())).encode("utf-8")


class handler(BaseHTTPRequestHandler):  # noqa: N801  (Vercel requires this name)
    def do_GET(self) -> None:  # noqa: N802
        body = _payload()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Deterministic output — safe to cache aggressively at the edge.
        self.send_header("Cache-Control", "public, max-age=60, s-maxage=300")
        self.end_headers()
        self.wfile.write(body)