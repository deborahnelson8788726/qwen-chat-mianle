#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/Users/ssdm4/qwen-chat-vercel"
WORKER="$BASE_DIR/telegram-bot/codex_relay_worker.py"
CFG_DIR="$HOME/.codex-relay"
ENV_FILE="${CODEX_RELAY_ENV_FILE:-$CFG_DIR/relay.env}"
LOG_DIR="$CFG_DIR/logs"

mkdir -p "$CFG_DIR" "$LOG_DIR"

echo "[relay-launch] starting at $(date '+%Y-%m-%d %H:%M:%S')"
echo "[relay-launch] env file: $ENV_FILE"

while true; do
  # Reset vars on each loop so config changes apply without restart.
  unset CODEX_RELAY_TOKEN CODEX_RELAY_WORKSPACE CODEX_RELAY_EXTRA_ARGS BOT_TOKEN || true

  if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a
    source "$ENV_FILE"
    set +a
  fi

  TOKEN="${CODEX_RELAY_TOKEN:-}"
  WORKSPACE="${CODEX_RELAY_WORKSPACE:-$BASE_DIR}"
  EXTRA_ARGS="${CODEX_RELAY_EXTRA_ARGS:-}"
  TG_TOKEN="${BOT_TOKEN:-}"

  if [[ -z "$TOKEN" || "$TOKEN" == "ML-XXXXXXXX" ]]; then
    echo "[relay-launch] CODEX_RELAY_TOKEN is not set in $ENV_FILE; sleeping 30s"
    sleep 30
    continue
  fi

  if [[ ! -x "$WORKER" ]]; then
    echo "[relay-launch] worker not executable: $WORKER; sleeping 30s"
    sleep 30
    continue
  fi

  echo "[relay-launch] running worker token=$TOKEN workspace=$WORKSPACE"
  if [[ -n "$EXTRA_ARGS" ]]; then
    python3 "$WORKER" \
      --token "$TOKEN" \
      --workspace "$WORKSPACE" \
      --bot-token "$TG_TOKEN" \
      --extra-args "$EXTRA_ARGS" \
      >>"$LOG_DIR/worker.log" 2>&1 || true
  else
    python3 "$WORKER" \
      --token "$TOKEN" \
      --workspace "$WORKSPACE" \
      --bot-token "$TG_TOKEN" \
      >>"$LOG_DIR/worker.log" 2>&1 || true
  fi

  echo "[relay-launch] worker exited; restart in 5s"
  sleep 5
done

