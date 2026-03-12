"""Perplexity web search proxy — Vercel serverless"""
from http.server import BaseHTTPRequestHandler
import json, urllib.request, urllib.error, ssl, socket

PPLX_URL = "https://api.perplexity.ai/chat/completions"
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

        key = d.get("key", "")
        query = d.get("query", "")
        if not key or not query:
            self._json(400, {"error": "missing key or query"})
            return

        payload = json.dumps({
            "model": "sonar",
            "messages": [
                {"role": "system", "content": "Ты помощник для веб-поиска. Отвечай кратко и информативно на русском языке. Приводи ключевые факты, даты, ссылки."},
                {"role": "user", "content": query}
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "return_citations": True,
            "search_recency_filter": "month"
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            PPLX_URL, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "Accept": "application/json"
            },
            method="POST"
        )

        try:
            resp = urllib.request.urlopen(req, timeout=30, context=SSL_CTX)
            data = json.loads(resp.read().decode("utf-8"))
            content = ""
            citations = []
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0].get("message", {}).get("content", "")
            if "citations" in data:
                citations = data["citations"]
            self._json(200, {"content": content, "citations": citations})
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            self._json(e.code, {"error": err})
        except urllib.error.URLError as e:
            self._json(502, {"error": str(e.reason)})
        except (TimeoutError, socket.timeout):
            self._json(504, {"error": "Perplexity timeout"})

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
