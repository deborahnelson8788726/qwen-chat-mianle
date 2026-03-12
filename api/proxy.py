"""NVIDIA API proxy — Vercel serverless"""
from http.server import BaseHTTPRequestHandler
import json, urllib.request, urllib.error, ssl, socket

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
SSL_CTX = ssl._create_unverified_context()

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

        key = d.pop("__api_key__", "")
        if not key:
            self._json(400, {"error": "no key"})
            return

        d["stream"] = True
        MAX_SYS, MAX_HIST, MAX_MSG = 24000, 10, 3000
        msgs = d.get("messages", [])
        for m in msgs:
            if m.get("role") == "system" and len(m.get("content", "")) > MAX_SYS:
                m["content"] = m["content"][:MAX_SYS] + "...\n[контекст обрезан]"
            if m.get("role") in ("user", "assistant") and len(m.get("content", "")) > MAX_MSG:
                m["content"] = m["content"][:MAX_MSG] + "..."
        sys_msgs = [m for m in msgs if m.get("role") == "system"]
        other = [m for m in msgs if m.get("role") != "system"]
        if len(other) > MAX_HIST:
            other = other[-MAX_HIST:]
        d["messages"] = sys_msgs + other

        payload = json.dumps(d, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            NVIDIA_URL, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "Accept": "text/event-stream"},
            method="POST"
        )

        try:
            resp = urllib.request.urlopen(req, timeout=120, context=SSL_CTX)
            data = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            self._json(e.code, {"error": err})
        except urllib.error.URLError as e:
            self._json(502, {"error": str(e.reason)})
        except (TimeoutError, socket.timeout):
            self._json(504, {"error": "Timeout"})

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
