"""Perplexity web search proxy — Vercel serverless"""
from http.server import BaseHTTPRequestHandler
import json, urllib.request, urllib.error, ssl, socket
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.monitor import capture

PPLX_URL = "https://api.perplexity.ai/chat/completions"
SSL_CTX = ssl._create_unverified_context()
DEFAULT_PPLX_KEY = os.getenv("PPLX_API_KEY", "").strip()


def _clean_domains(raw):
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for x in raw:
        if not isinstance(x, str):
            continue
        d = x.strip().lower()
        if not d:
            continue
        d = d.replace("https://", "").replace("http://", "").split("/")[0]
        if d.startswith("www."):
            d = d[4:]
        if not d or "." not in d or d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out[:6]


def _pplx_request(key, payload_obj):
    payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        PPLX_URL, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "Accept": "application/json"
        },
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=30, context=SSL_CTX)
    return json.loads(resp.read().decode("utf-8"))

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

        key = (d.get("key", "") or "").strip() or DEFAULT_PPLX_KEY
        query = d.get("query", "")
        if not key or not query:
            self._json(400, {"error": "missing query or PPLX_API_KEY is not configured"})
            return

        domains = _clean_domains(d.get("domains", []))
        payload_obj = {
            "model": "sonar",
            "messages": [
                {"role": "system", "content": "Ты помощник для веб-поиска. Отвечай строго по найденным источникам, без догадок. Обязательно указывай факты, даты и ссылки. Если данных недостаточно — так и скажи."},
                {"role": "user", "content": query}
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "return_citations": True
        }
        if domains:
            payload_obj["search_domain_filter"] = domains

        try:
            try:
                data = _pplx_request(key, payload_obj)
            except urllib.error.HTTPError:
                # Fallback if provider rejects domain filter field for a model/plan.
                if "search_domain_filter" in payload_obj:
                    payload_obj.pop("search_domain_filter", None)
                    data = _pplx_request(key, payload_obj)
                else:
                    raise
            content = ""
            citations = []
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0].get("message", {}).get("content", "")
            if "citations" in data:
                citations = data["citations"]
            self._json(200, {
                "content": content,
                "citations": citations,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            })
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            capture(e, "api.search.http_error", {"status": e.code})
            self._json(e.code, {"error": err})
        except urllib.error.URLError as e:
            capture(e, "api.search.url_error")
            self._json(502, {"error": str(e.reason)})
        except (TimeoutError, socket.timeout):
            capture(TimeoutError("Perplexity timeout"), "api.search.timeout")
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
