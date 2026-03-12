"""POST /api/files/toggle-all"""
from http.server import BaseHTTPRequestHandler
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _storage import toggle_all

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        d = json.loads(body) if body else {}
        en = d.get("enabled", True)
        res = toggle_all(en)
        b = json.dumps(res, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args): pass
