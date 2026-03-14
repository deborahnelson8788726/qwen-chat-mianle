"""Codex relay queue API.

POST /api/codex
  - action=enqueue  {token, task, chat_id, user_id, username}
  - action=claim    {token, worker_id, stale_sec}
  - action=complete {token, id, status, result, worker_id}
  - action=cancel   {token, id}

GET /api/codex?token=...&action=list&limit=20
GET /api/codex?token=...&action=get&id=...

Storage: Redis (when REDIS_URL configured) with /tmp fallback.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import re
import sys
import time
import urllib.parse
import secrets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.kv import get_json, set_json
from lib.monitor import capture

QUEUE_DIR = "/tmp/milean_codex_queue"
_MAX_AGE = 86400 * 7  # 7 days
_MAX_TASKS = 300
_cache = {}


def _ensure_dir():
    os.makedirs(QUEUE_DIR, exist_ok=True)


def _safe_token(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", token)


def _path(token: str) -> str:
    return os.path.join(QUEUE_DIR, f"{_safe_token(token)}.json")


def _redis_key(token: str) -> str:
    return f"milean:codexq:{token}"


def _now() -> float:
    return time.time()


def _new_queue() -> dict:
    return {"ts": _now(), "tasks": []}


def _cleanup_tasks(q: dict):
    now = _now()
    tasks = q.get("tasks", [])
    kept = []
    for t in tasks:
        created = float(t.get("created_at", 0) or 0)
        updated = float(t.get("updated_at", created) or created)
        age = now - (updated or created or now)
        if age <= _MAX_AGE:
            kept.append(t)
    kept.sort(key=lambda x: float(x.get("created_at", 0) or 0))
    if len(kept) > _MAX_TASKS:
        kept = kept[-_MAX_TASKS:]
    q["tasks"] = kept
    q["ts"] = now


def _load(token: str) -> dict:
    if token in _cache:
        q = _cache[token]
        _cleanup_tasks(q)
        return q
    rq = get_json(_redis_key(token))
    if isinstance(rq, dict):
        if not isinstance(rq.get("tasks"), list):
            rq["tasks"] = []
        _cleanup_tasks(rq)
        _cache[token] = rq
        return rq
    p = _path(token)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                q = json.load(f)
            if not isinstance(q, dict):
                q = _new_queue()
            if not isinstance(q.get("tasks"), list):
                q["tasks"] = []
            _cleanup_tasks(q)
            _cache[token] = q
            return q
        except Exception:
            pass
    q = _new_queue()
    _cache[token] = q
    return q


def _save(token: str, q: dict):
    q["ts"] = _now()
    set_json(_redis_key(token), q, ttl_sec=_MAX_AGE)
    _ensure_dir()
    p = _path(token)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(q, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass
    _cache[token] = q


def _new_id() -> str:
    return f"cx-{int(_now())}-{secrets.token_hex(3)}"


def _enqueue(token: str, d: dict) -> dict:
    q = _load(token)
    task = (d.get("task") or "").strip()
    if not task:
        raise ValueError("missing task")
    if len(task) > 6000:
        task = task[:6000]
    now = _now()
    t = {
        "id": _new_id(),
        "task": task,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "chat_id": d.get("chat_id"),
        "user_id": d.get("user_id"),
        "username": d.get("username", ""),
        "source": d.get("source", "telegram"),
    }
    q["tasks"].append(t)
    _cleanup_tasks(q)
    _save(token, q)
    return t


def _claim(token: str, d: dict):
    q = _load(token)
    now = _now()
    worker_id = (d.get("worker_id") or "").strip() or "worker"
    stale_sec = int(d.get("stale_sec") or 1800)

    # Requeue stale claimed tasks
    for t in q["tasks"]:
        if t.get("status") == "claimed":
            claimed_at = float(t.get("claimed_at", 0) or 0)
            if claimed_at and now - claimed_at > stale_sec:
                t["status"] = "queued"
                t.pop("claimed_at", None)
                t.pop("claimed_by", None)
                t["updated_at"] = now

    # FIFO by created time
    q["tasks"].sort(key=lambda x: float(x.get("created_at", 0) or 0))
    picked = None
    for t in q["tasks"]:
        if t.get("status") == "queued":
            t["status"] = "claimed"
            t["claimed_at"] = now
            t["claimed_by"] = worker_id
            t["updated_at"] = now
            picked = t
            break
    _cleanup_tasks(q)
    _save(token, q)
    return picked


def _complete(token: str, d: dict):
    q = _load(token)
    tid = (d.get("id") or "").strip()
    if not tid:
        raise ValueError("missing id")
    status = (d.get("status") or "done").strip().lower()
    if status not in ("done", "error", "skipped"):
        status = "done"
    now = _now()
    found = None
    for t in q["tasks"]:
        if t.get("id") == tid:
            t["status"] = status
            t["result"] = (d.get("result") or "")[:8000]
            t["completed_at"] = now
            t["updated_at"] = now
            if d.get("worker_id"):
                t["completed_by"] = d.get("worker_id")
            found = t
            break
    if not found:
        raise ValueError("task not found")
    _cleanup_tasks(q)
    _save(token, q)
    return found


def _cancel(token: str, d: dict):
    q = _load(token)
    tid = (d.get("id") or "").strip()
    if not tid:
        raise ValueError("missing id")
    now = _now()
    found = None
    for t in q["tasks"]:
        if t.get("id") == tid and t.get("status") in ("queued", "claimed"):
            t["status"] = "canceled"
            t["updated_at"] = now
            found = t
            break
    if not found:
        raise ValueError("task not cancelable")
    _cleanup_tasks(q)
    _save(token, q)
    return found


def _list(token: str, limit: int = 20):
    q = _load(token)
    limit = max(1, min(100, int(limit or 20)))
    tasks = list(q.get("tasks", []))
    tasks.sort(key=lambda x: float(x.get("created_at", 0) or 0), reverse=True)
    return tasks[:limit]


def _get(token: str, tid: str):
    q = _load(token)
    for t in q.get("tasks", []):
        if t.get("id") == tid:
            return t
    return None


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            d = json.loads(body) if body else {}
        except Exception as e:
            self._json(400, {"error": f"Bad JSON: {e}"})
            return

        token = (d.get("token") or "").strip()
        if not token or len(token) < 6:
            self._json(400, {"error": "Invalid token"})
            return
        action = (d.get("action") or "enqueue").strip().lower()
        try:
            if action == "enqueue":
                t = _enqueue(token, d)
                self._json(200, {"ok": True, "task": t})
                return
            if action == "claim":
                t = _claim(token, d)
                self._json(200, {"ok": True, "task": t})
                return
            if action == "complete":
                t = _complete(token, d)
                self._json(200, {"ok": True, "task": t})
                return
            if action == "cancel":
                t = _cancel(token, d)
                self._json(200, {"ok": True, "task": t})
                return
            self._json(400, {"error": "Unknown action"})
        except Exception as e:
            capture(e, f"api.codex.post.{action}")
            self._json(400, {"error": str(e)})

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        p = urllib.parse.parse_qs(qs)
        token = (p.get("token", [""])[0] or "").strip()
        if not token:
            self._json(400, {"error": "No token"})
            return
        action = (p.get("action", ["list"])[0] or "list").strip().lower()
        try:
            if action == "list":
                try:
                    limit = int((p.get("limit", ["20"])[0] or "20"))
                except Exception:
                    limit = 20
                self._json(200, {"ok": True, "tasks": _list(token, limit)})
                return
            if action == "get":
                tid = (p.get("id", [""])[0] or "").strip()
                if not tid:
                    self._json(400, {"error": "No id"})
                    return
                t = _get(token, tid)
                if not t:
                    self._json(404, {"error": "Task not found"})
                    return
                self._json(200, {"ok": True, "task": t})
                return
            self._json(400, {"error": "Unknown action"})
        except Exception as e:
            capture(e, f"api.codex.get.{action}")
            self._json(500, {"error": "Queue read failure"})

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
