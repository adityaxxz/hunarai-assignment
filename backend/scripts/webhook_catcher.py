"""Catch and dump Hunar webhooks verbatim. Development tool, not part of the app.

    cd backend && uv run python -m scripts.webhook_catcher

Listens on port 8787, accepts POST on any path, writes one JSON file per request
to backend/fixtures/raw/ and returns 200 immediately. It does not verify
signatures on purpose: the point is to capture what Hunar actually sends,
including the signature headers, so task 5 can be written and tested against real
payloads instead of a guess.

Expose it with:  cloudflared tunnel --url http://localhost:8787
"""

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8787
RAW_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "raw"

_counter = 0
_lock = threading.Lock()


def next_index() -> int:
    global _counter
    with _lock:
        _counter += 1
        return _counter


def summarise(body: str) -> str:
    """Best-effort one-line view of the payload so the console is watchable live."""
    try:
        payload = json.loads(body)
    except ValueError:
        return f"{len(body)} bytes (not json)"
    if not isinstance(payload, dict):
        return f"{len(body)} bytes"
    interesting = ("event", "event_type", "status", "lifecycle_status", "id", "call_id")
    bits = [f"{k}={payload[k]}" for k in interesting if payload.get(k) is not None]
    return f"{len(body)} bytes  " + "  ".join(bits)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802  (stdlib naming)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        received_at = datetime.now(timezone.utc).isoformat()
        index = next_index()

        capture = {
            "index": index,
            "received_at": received_at,
            "method": "POST",
            "path": self.path,
            # Verbatim, including X-Hunar-Signature and X-Hunar-Timestamp, which
            # are the whole reason this exists.
            "headers": dict(self.headers.items()),
            "body": raw.decode("utf-8", errors="replace"),
        }

        slug = self.path.strip("/").replace("/", "-") or "root"
        path = RAW_DIR / f"webhook-{index:03d}-{slug}.json"
        path.write_text(json.dumps(capture, indent=2), encoding="utf-8")

        self._respond(200, b'{"ok":true}')
        print(
            f"[{received_at[11:19]}] #{index:03d} POST {self.path}  "
            f"{summarise(capture['body'])}  -> {path.name}",
            flush=True,
        )

    def do_GET(self) -> None:  # noqa: N802
        # Only so you can confirm the tunnel is actually reaching this process.
        self._respond(200, b'{"catcher":"alive"}')

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass  # replaced by the one-liner above


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing captures to {RAW_DIR}")
    print(f"Listening on http://localhost:{PORT} (POST any path). Ctrl+C to stop.\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
