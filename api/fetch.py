"""Fetch and extract text from web pages by URL (safe subset)."""
from http.server import BaseHTTPRequestHandler
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

SSL_CTX = ssl._create_unverified_context()
MAX_URLS = 3
MAX_BODY_BYTES = 900_000
MAX_TEXT_CHARS = 9_000
TIMEOUT = 15


class _HtmlTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._capture_title = False
        self.title = ""
        self.parts = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg", "canvas", "iframe"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "title":
            self._capture_title = True
        if t in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg", "canvas", "iframe"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if t == "title":
            self._capture_title = False
        if self._skip_depth == 0 and t in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not data:
            return
        if self._capture_title:
            self.title += data
        if self._skip_depth > 0:
            return
        self.parts.append(data)


def _normalize_space(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _host_is_public(host: str) -> bool:
    if not host:
        return False
    h = host.strip().lower().strip(".")
    if h in ("localhost",) or h.endswith(".local") or h.endswith(".internal"):
        return False

    try:
        ip = ipaddress.ip_address(h)
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(h, None)
    except Exception:
        return False
    if not infos:
        return False

    for inf in infos:
        addr = inf[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _extract_html(raw: bytes, ct: str):
    charset = "utf-8"
    m = re.search(r"charset=([a-zA-Z0-9._-]+)", ct or "", re.I)
    if m:
        charset = m.group(1).lower()
    html = raw.decode(charset, errors="replace")
    p = _HtmlTextExtractor()
    p.feed(html)
    title = _normalize_space(p.title)[:300]
    text = _normalize_space("".join(p.parts))
    return title, text


def _extract_pdf(raw: bytes):
    if PdfReader is None:
        return "", ""
    try:
        reader = PdfReader(BytesIO(raw))
    except Exception:
        return "", ""
    chunks = []
    for page in reader.pages[:8]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "", _normalize_space("\n".join(chunks))


def _extract_plain(raw: bytes, ct: str):
    charset = "utf-8"
    m = re.search(r"charset=([a-zA-Z0-9._-]+)", ct or "", re.I)
    if m:
        charset = m.group(1).lower()
    text = raw.decode(charset, errors="replace")
    return "", _normalize_space(text)


def _fetch_one(url: str):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("unsupported scheme")
    if not _host_is_public(parsed.hostname or ""):
        raise ValueError("host is not allowed")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MileanBot/1.0; +https://milean.vercel.app)",
            "Accept": "text/html, text/plain, application/pdf;q=0.9, */*;q=0.5",
        },
        method="GET",
    )
    resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX)
    ct = (resp.headers.get("Content-Type") or "").lower()
    raw = resp.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raw = raw[:MAX_BODY_BYTES]

    title = ""
    text = ""
    is_pdf = "application/pdf" in ct or parsed.path.lower().endswith(".pdf")
    if "text/html" in ct:
        title, text = _extract_html(raw, ct)
    elif is_pdf:
        title, text = _extract_pdf(raw)
    elif "text/" in ct or not ct:
        title, text = _extract_plain(raw, ct)
    else:
        title, text = _extract_plain(raw, ct)

    text = text[:MAX_TEXT_CHARS].strip()
    if not text:
        raise ValueError("empty or unsupported content")

    return {
        "url": url,
        "title": title,
        "content_type": ct,
        "chars": len(text),
        "content": text,
    }


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

        urls = d.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list):
            self._json(400, {"error": "urls must be list"})
            return

        cleaned = []
        seen = set()
        for u in urls:
            if not isinstance(u, str):
                continue
            u2 = u.strip()
            if not u2 or u2 in seen:
                continue
            seen.add(u2)
            cleaned.append(u2)
        cleaned = cleaned[:MAX_URLS]
        if not cleaned:
            self._json(400, {"error": "no urls"})
            return

        pages = []
        failed = []
        for u in cleaned:
            try:
                pages.append(_fetch_one(u))
            except urllib.error.HTTPError as e:
                failed.append({"url": u, "error": f"http {e.code}"})
            except urllib.error.URLError as e:
                failed.append({"url": u, "error": str(e.reason)})
            except (TimeoutError, socket.timeout):
                failed.append({"url": u, "error": "timeout"})
            except Exception as e:
                failed.append({"url": u, "error": str(e)})

        self._json(200, {"pages": pages, "failed": failed})

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
