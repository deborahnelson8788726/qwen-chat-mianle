"""POST /api/files/delete?id=xxx"""
from http.server import BaseHTTPRequestHandler
import json, sys, os
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.storage import delete_file

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        qs = parse_qs(urlparse(self.path).query)
        fid = qs.get("id", [""])[0]
        res = delete_file(fid)
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
