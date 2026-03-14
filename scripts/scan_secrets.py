#!/usr/bin/env python3
"""Simple staged-secret scanner for pre-commit hook.

Scans staged file content (from git index) and blocks commit on known secret patterns.
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Iterable


PATTERNS = [
    ("NVIDIA key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}\b")),
    ("Perplexity key", re.compile(r"\bpplx-[A-Za-z0-9_-]{20,}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{30,}\b")),
    ("OpenAI style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Bearer token", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE)),
]

ALLOW_MARKERS = (
    "xxxxxxxx",
    "telegram_bot_token_here",
    "example",
    "placeholder",
)


def _run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)


def staged_files() -> list[str]:
    out = _run("git", "diff", "--cached", "--name-only", "--diff-filter=ACM")
    files = [x.strip() for x in out.splitlines() if x.strip()]
    # Skip obvious binaries/archives
    deny_ext = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".woff", ".woff2")
    return [f for f in files if not f.lower().endswith(deny_ext)]


def read_staged(path: str) -> str:
    try:
        return _run("git", "show", f":{path}")
    except Exception:
        return ""


def has_allow_marker(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in ALLOW_MARKERS)


def find_hits(path: str, content: str) -> Iterable[str]:
    for label, rx in PATTERNS:
        for m in rx.finditer(content):
            token = m.group(0)
            if has_allow_marker(token):
                continue
            yield f"{path}: {label}: {token[:14]}...{token[-6:]}"


def main() -> int:
    files = staged_files()
    if not files:
        return 0

    hits: list[str] = []
    for path in files:
        content = read_staged(path)
        if not content:
            continue
        hits.extend(find_hits(path, content))

    if hits:
        print("ERROR: potential secrets detected in staged files:")
        for h in hits:
            print(f" - {h}")
        print("\nCommit blocked. Move secrets to .env and retry.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
