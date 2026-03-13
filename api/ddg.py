"""DuckDuckGo web search proxy — Vercel serverless, no API key needed"""
from http.server import BaseHTTPRequestHandler
import json, urllib.request, urllib.error, ssl, socket, re
from html.parser import HTMLParser

SSL_CTX = ssl._create_unverified_context()
DDG_HTML = "https://html.duckduckgo.com/html/"

class TextExtractor(HTMLParser):
    """Extract search results from DuckDuckGo HTML"""
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = {}
        self.capture = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        cls = attrs_d.get("class", "")
        if tag == "a" and "result__a" in cls:
            self.current["title"] = ""
            self.current["url"] = attrs_d.get("href", "")
            self.capture = "title"
        elif tag == "a" and "result__snippet" in cls:
            self.current["snippet"] = ""
            self.capture = "snippet"

    def handle_endtag(self, tag):
        if tag == "a" and self.capture in ("title", "snippet"):
            if self.capture == "snippet" and self.current.get("title"):
                self.results.append(dict(self.current))
                self.current = {}
            self.capture = None

    def handle_data(self, data):
        if self.capture == "title":
            self.current["title"] = self.current.get("title", "") + data
        elif self.capture == "snippet":
            self.current["snippet"] = self.current.get("snippet", "") + data


def ddg_search(query, num=8):
    """Search DuckDuckGo HTML version and parse results"""
    data = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
    req = urllib.request.Request(
        DDG_HTML, data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=15, context=SSL_CTX)
    html = resp.read().decode("utf-8", errors="replace")

    parser = TextExtractor()
    parser.feed(html)
    results = parser.results[:num]

    # Clean URLs (DDG wraps them)
    for r in results:
        url = r.get("url", "")
        m = re.search(r'uddg=([^&]+)', url)
        if m:
            r["url"] = urllib.parse.unquote(m.group(1))
    return results


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

        query = d.get("query", "").strip()
        if not query:
            self._json(400, {"error": "no query"})
            return

        try:
            results = ddg_search(query, num=8)
            content = ""
            citations = []
            for i, r in enumerate(results):
                title = r.get("title", "").strip()
                snippet = r.get("snippet", "").strip()
                url = r.get("url", "")
                content += f"[{i+1}] {title}\n{snippet}\n\n"
                if url:
                    citations.append(url)
            self._json(200, {"content": content.strip(), "citations": citations})
        except Exception as e:
            self._json(502, {"error": f"DDG search error: {e}"})

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
