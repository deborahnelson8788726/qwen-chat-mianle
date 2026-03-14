"""GET /api/stats"""
from http.server import BaseHTTPRequestHandler
import json, sys, os, traceback
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.storage import get_stats
from lib.kv import redis_available

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            data = get_stats()
            data["ok"] = True
            data["time_utc"] = datetime.now(timezone.utc).isoformat()
            data["redis"] = "up" if redis_available() else "down"
            b = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b)
        except Exception as e:
            b = json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode()
            self.send_response(500)
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
