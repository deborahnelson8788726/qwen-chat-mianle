#!/usr/bin/env python3
"""Codex relay worker.

Polls /api/codex queue, runs tasks via `codex exec`, marks completion,
and optionally posts progress/results back to Telegram chat.
"""
import argparse
import html
import json
import os
import shlex
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any


def http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 30):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"ok": False, "error": f"Bad JSON response: {raw[:200]}"}


def send_telegram(bot_token: str, chat_id: int, text: str):
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        http_json("POST", url, payload, timeout=20)
    except Exception:
        pass


def format_tail(text: str, limit: int = 1800) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit] + "\n..."
    return html.escape(text)


def run_codex(task: str, workspace: str, timeout_sec: int, extra_args: list[str]):
    with tempfile.NamedTemporaryFile(prefix="codex-last-", suffix=".txt", delete=False) as f:
        out_path = f.name
    cmd = ["codex", "exec", "--full-auto", "--search", "-C", workspace, "-o", out_path]
    cmd.extend(extra_args)
    cmd.append(task)
    started = time.time()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    elapsed = int(time.time() - started)
    last_msg = ""
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            last_msg = f.read()
    except Exception:
        pass
    try:
        os.remove(out_path)
    except Exception:
        pass
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "last_message": last_msg or "",
        "elapsed_sec": elapsed,
        "cmd": " ".join(shlex.quote(x) for x in cmd),
    }


def claim_task(api_url: str, token: str, worker_id: str, stale_sec: int):
    payload = {"token": token, "action": "claim", "worker_id": worker_id, "stale_sec": stale_sec}
    data = http_json("POST", api_url, payload, timeout=30)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "claim failed")
    return data.get("task")


def complete_task(api_url: str, token: str, task_id: str, status: str, result: str, worker_id: str):
    payload = {
        "token": token,
        "action": "complete",
        "id": task_id,
        "status": status,
        "result": result,
        "worker_id": worker_id,
    }
    data = http_json("POST", api_url, payload, timeout=30)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "complete failed")
    return data.get("task")


def main():
    ap = argparse.ArgumentParser(description="Codex relay worker for Telegram queue")
    ap.add_argument("--token", required=True, help="Project token (ML-XXXXXXXX)")
    ap.add_argument("--api-url", default="https://milean.vercel.app/api/codex", help="Queue API URL")
    ap.add_argument("--workspace", default=os.getcwd(), help="Workspace path for codex exec")
    ap.add_argument("--poll-sec", type=float, default=4.0, help="Polling interval")
    ap.add_argument("--stale-sec", type=int, default=1800, help="Requeue stale claimed tasks after seconds")
    ap.add_argument("--timeout-sec", type=int, default=1800, help="Timeout for one codex task")
    ap.add_argument("--bot-token", default=os.getenv("BOT_TOKEN", ""), help="Telegram bot token for notifications")
    ap.add_argument("--once", action="store_true", help="Process at most one task then exit")
    ap.add_argument("--dry-run", action="store_true", help="Do not execute codex, just mark tasks as done")
    ap.add_argument("--extra-args", default=os.getenv("CODEX_RELAY_EXTRA_ARGS", ""), help="Extra args for codex exec")
    args = ap.parse_args()

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    extra_args = shlex.split(args.extra_args) if args.extra_args else []

    print(f"[relay] started worker={worker_id} token={args.token} workspace={args.workspace}")
    while True:
        try:
            task = claim_task(args.api_url, args.token, worker_id, args.stale_sec)
        except Exception as e:
            print(f"[relay] claim error: {e}")
            time.sleep(max(args.poll_sec, 5))
            continue

        if not task:
            if args.once:
                print("[relay] no queued tasks")
                return
            time.sleep(args.poll_sec)
            continue

        tid = task.get("id")
        ttext = (task.get("task") or "").strip()
        chat_id = task.get("chat_id")
        print(f"[relay] claimed {tid}: {ttext[:120]}")
        send_telegram(args.bot_token, chat_id, f"🛠 <b>Codex принял задачу</b>\n🆔 <code>{html.escape(str(tid))}</code>")

        if args.dry_run:
            result = "Dry-run: task accepted by relay worker."
            try:
                complete_task(args.api_url, args.token, tid, "done", result, worker_id)
            except Exception as e:
                print(f"[relay] complete error: {e}")
            send_telegram(args.bot_token, chat_id, f"✅ <b>Codex dry-run завершён</b>\n🆔 <code>{html.escape(str(tid))}</code>")
            if args.once:
                return
            continue

        status = "done"
        summary = ""
        try:
            out = run_codex(ttext, args.workspace, args.timeout_sec, extra_args)
            if out["returncode"] != 0:
                status = "error"
            last_msg = out["last_message"].strip() or out["stderr"].strip() or out["stdout"].strip()
            summary = (
                f"rc={out['returncode']} elapsed={out['elapsed_sec']}s\n"
                f"cmd={out['cmd']}\n\n{last_msg}"
            )
        except subprocess.TimeoutExpired:
            status = "error"
            summary = f"Timeout after {args.timeout_sec}s"
        except Exception as e:
            status = "error"
            summary = f"Worker error: {e}"

        try:
            complete_task(args.api_url, args.token, tid, status, summary, worker_id)
        except Exception as e:
            print(f"[relay] complete error: {e}")

        short = format_tail(summary, 2000)
        if status == "done":
            send_telegram(
                args.bot_token,
                chat_id,
                f"✅ <b>Codex завершил задачу</b>\n🆔 <code>{html.escape(str(tid))}</code>\n\n<blockquote>{short}</blockquote>",
            )
        else:
            send_telegram(
                args.bot_token,
                chat_id,
                f"❌ <b>Codex ошибка</b>\n🆔 <code>{html.escape(str(tid))}</code>\n\n<blockquote>{short}</blockquote>",
            )

        if args.once:
            return


if __name__ == "__main__":
    main()
