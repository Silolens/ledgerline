"""Read-only database connectivity probe for Vercel Preview/Development.

This endpoint never writes to the ledger and never returns connection details.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        database_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
        if not database_url:
            self._respond(503, {"status": "unavailable", "reason": "database_not_configured"})
            return

        try:
            import psycopg

            with psycopg.connect(
                database_url,
                connect_timeout=5,
                sslmode="require",
                options="-c statement_timeout=5000",
                prepare_threshold=None,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    row = cursor.fetchone()
                    if row is None or row[0] != 1:
                        raise RuntimeError("database_probe_failed")
        except Exception:
            # Do not expose hostnames, SQL, credentials, or provider error details.
            self._respond(503, {"status": "unavailable", "reason": "database_connection_failed"})
            return

        self._respond(200, {"status": "ok", "database": "reachable", "writes_performed": False})

    def _respond(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
