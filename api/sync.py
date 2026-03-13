"""Project sync API — bridge between Web and Telegram bot.
POST /api/sync — store project data by token
GET /api/sync?token=XXX — retrieve project data by token
"""
from http.server import BaseHTTPRequestHandler
import json
import urllib.parse
import time

# In-memory store (persists within serverless instance warm period)
_store = {}
_MAX_AGE = 86400  # 24 hours
_MAX_STORE = 200  # max projects in memory


def _cleanup():
    """Remove expired entries"""
    now = time.time()
    expired = [k for k, v in _store.items() if now - v.get("ts", 0) > _MAX_AGE]
    for k in expired:
        del _store[k]
    # If still too many, remove oldest
    if len(_store) > _MAX_STORE:
        items = sorted(_store.items(), key=lambda x: x[1].get("ts", 0))
        for k, _ in items[:len(items) - _MAX_STORE]:
            del _store[k]


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            d = json.loads(body)
        except Exception as e:
            self._json(400, {"error": f"Bad JSON: {e}"})
            return

        token = d.get("token", "").strip()
        if not token or len(token) < 6:
            self._json(400, {"error": "Invalid token"})
            return

        _cleanup()

        _store[token] = {
            "ts": time.time(),
            "name": d.get("name", "Project"),
            "instr": d.get("instr", ""),
            "files": d.get("files", []),  # [{name, chunks, chars}]
            "chunks": d.get("chunks", []),  # [{text, file}]
            "hist": d.get("hist", []),
        }

        self._json(200, {"ok": True, "token": token, "stored": len(_store)})

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        token = params.get("token", [""])[0].strip()

        if not token:
            self._json(400, {"error": "No token"})
            return

        data = _store.get(token)
        if not data:
            self._json(404, {"error": "Project not found or expired"})
            return

        self._json(200, {
            "ok": True,
            "name": data["name"],
            "instr": data["instr"],
            "files": data["files"],
            "chunks": data["chunks"],
            "hist": data["hist"],
        })

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
