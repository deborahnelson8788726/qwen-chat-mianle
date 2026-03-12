"""GET /api/stats"""
from http.server import BaseHTTPRequestHandler
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _storage import get_stats

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = get_stats()
        b = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, fmt, *args): pass
