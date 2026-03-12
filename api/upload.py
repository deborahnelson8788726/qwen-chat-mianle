"""POST /api/upload — multipart file upload"""
from http.server import BaseHTTPRequestHandler
import json, re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _storage import upload_files

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            ct = self.headers.get("Content-Type", "")
            bnd = None
            for p in ct.split(";"):
                p = p.strip()
                if p.startswith("boundary="):
                    bnd = p[9:].strip().strip('"')
            if not bnd:
                self._json(400, {"error": "no boundary"})
                return

            length = int(self.headers.get("Content-Length", 0))
            if length > 4 * 1024 * 1024:
                self._json(413, {"error": "Файл слишком большой (макс. 4MB)"})
                return

            body = self.rfile.read(length)
            files = parse_multipart(body, bnd)
            if not files:
                self._json(400, {"error": "no files"})
                return

            uploaded = upload_files(files)
            b = json.dumps({"uploaded": uploaded}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b)
        except Exception as e:
            import traceback
            self._json(500, {"error": str(e), "trace": traceback.format_exc()})

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
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


def parse_multipart(body, boundary):
    res = []
    delim = b"--" + (boundary.encode() if isinstance(boundary, str) else boundary)
    for part in body.split(delim):
        if not part or part.strip() in (b"--", b""):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        sep = part.find(b"\r\n\r\n")
        if sep == -1:
            continue
        hdr = part[:sep].decode("utf-8", errors="replace")
        data = part[sep + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        m = re.search(r'filename="([^"]*)"', hdr)
        if m and m.group(1) and data:
            res.append((m.group(1), data))
    return res
