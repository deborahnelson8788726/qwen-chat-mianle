"""Project sync API — bridge between Web and Telegram bot.
POST /api/sync — store project data by token
GET /api/sync?token=XXX — retrieve project data by token

Uses Redis when REDIS_URL is configured (primary persistence).
Falls back to /tmp for local/serverless best-effort.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import urllib.parse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.kv import get_json, set_json, redis_available
from lib.monitor import capture

SYNC_DIR = "/tmp/milean_sync"
_cache = {}
_MAX_AGE = 86400 * 3  # 3 days


def _ensure_dir():
    os.makedirs(SYNC_DIR, exist_ok=True)


def _token_path(token):
    safe = token.replace("/", "_").replace("\\", "_")
    return os.path.join(SYNC_DIR, f"{safe}.json")


def _save(token, data):
    data["ts"] = time.time()
    # Redis primary
    set_json(f"milean:sync:{token}", data, ttl_sec=_MAX_AGE)
    # Fallback file storage
    _ensure_dir()
    path = _token_path(token)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    _cache[token] = data


def _load(token):
    # Try cache first
    if token in _cache:
        d = _cache[token]
        if time.time() - d.get("ts", 0) < _MAX_AGE:
            return d
        else:
            del _cache[token]

    # Try Redis
    d = get_json(f"milean:sync:{token}")
    if isinstance(d, dict) and time.time() - d.get("ts", 0) < _MAX_AGE:
        _cache[token] = d
        return d

    # Try file
    path = _token_path(token)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if time.time() - d.get("ts", 0) < _MAX_AGE:
                _cache[token] = d
                return d
            else:
                os.remove(path)
        except:
            pass
    return None


def _cleanup():
    if redis_available():
        return
    _ensure_dir()
    now = time.time()
    try:
        for fn in os.listdir(SYNC_DIR):
            fp = os.path.join(SYNC_DIR, fn)
            try:
                with open(fp, "r") as f:
                    d = json.load(f)
                if now - d.get("ts", 0) > _MAX_AGE:
                    os.remove(fp)
            except:
                pass
    except:
        pass


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

        data = {
            "ts": time.time(),
            "name": d.get("name", "Project"),
            "instr": d.get("instr", ""),
            "files": d.get("files", []),
            "chunks": d.get("chunks", []),
            "hist": d.get("hist", []),
        }

        try:
            _save(token, data)
            _cleanup()
            self._json(200, {"ok": True, "token": token})
        except Exception as e:
            capture(e, "api.sync.post")
            self._json(500, {"error": "Sync storage failure"})

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        token = params.get("token", [""])[0].strip()

        if not token:
            self._json(400, {"error": "No token"})
            return

        try:
            data = _load(token)
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
        except Exception as e:
            capture(e, "api.sync.get")
            self._json(500, {"error": "Sync read failure"})

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
