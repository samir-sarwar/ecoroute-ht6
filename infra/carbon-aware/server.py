from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self.respond({"status": "ok"})
            return
        if parsed.path == "/emissions/bylocation":
            zone = parse_qs(parsed.query).get("location", ["demo-local"])[0]
            rating = 80.0 if zone == "demo-remote" else 275.0
            self.respond(
                [
                    {
                        "location": zone,
                        "time": datetime.now(timezone.utc).isoformat(),
                        "rating": rating,
                        "source": "ecoroute-carbon-aware-fixture",
                    }
                ]
            )
            return
        self.respond({"error": "not_found"}, 404)

    def respond(self, body: object, status: int = 200) -> None:
        value = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(value)))
        self.end_headers()
        self.wfile.write(value)

    def log_message(self, format: str, *args: object) -> None:
        return


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
