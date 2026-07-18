from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "ecoroute-kernel-lab"
ITERATIONS = int(os.getenv("ECOROUTE_LAB_PBKDF2_ITERATIONS", "180000"))


class KernelLabServer(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "EcoRouteKernelLab/1.0"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"", "/healthz"}:
            self._send(200, {"status": "healthy", "model": MODEL, "pid": os.getpid()})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL,
                            "object": "model",
                            "created": 0,
                            "owned_by": "ecoroute",
                        }
                    ],
                },
            )
            return
        self._send(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": {"message": "Not found"}})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": {"message": "Invalid JSON"}})
            return
        messages = payload.get("messages") or []
        prompt = "\n".join(
            str(item.get("content", "")) for item in messages if isinstance(item, dict)
        )
        started = time.perf_counter()
        # PBKDF2 provides deterministic CPU-bound work and releases the GIL, allowing
        # concurrent requests to exercise multiple guest vCPUs in one inference process.
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            prompt.encode() or b"empty",
            b"ecoroute-kernel-lab",
            ITERATIONS,
            dklen=32,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        prompt_tokens = max(1, len(prompt.split()))
        content = f"Kernel lab response {digest.hex()[:12]} ({elapsed_ms} ms CPU workload)."
        self._send(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 14,
                    "total_tokens": prompt_tokens + 14,
                },
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


if __name__ == "__main__":
    host = os.getenv("ECOROUTE_LAB_HOST", "0.0.0.0")
    port = int(os.getenv("ECOROUTE_LAB_PORT", "9100"))
    print(
        f"EcoRoute kernel lab target listening on {host}:{port}; "
        f"pid={os.getpid()} iterations={ITERATIONS}",
        flush=True,
    )
    KernelLabServer((host, port), Handler).serve_forever()
