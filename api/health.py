"""Health endpoint for uptime checks."""
from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timezone
from _kv import redis_available


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        payload = {
            "ok": True,
            "service": "milean-api",
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "redis": "up" if redis_available() else "down",
            "env": {
                "has_nvidia_key": bool((os.getenv("NVIDIA_API_KEY", "") or "").strip()),
                "has_pplx_key": bool((os.getenv("PPLX_API_KEY", "") or "").strip()),
                "has_sentry": bool((os.getenv("SENTRY_DSN", "") or "").strip()),
            },
        }
        self._json(200, payload)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt, *args):
        pass
