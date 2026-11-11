#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-7860}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -f ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"
else
  PY="${PYTHON:-python3}"
fi

SERVER_PID=""
TUNNEL_PID=""

cleanup() {
  if [[ -n "$TUNNEL_PID" ]]; then kill "$TUNNEL_PID" 2>/dev/null || true; fi
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; fi
}
trap cleanup INT TERM EXIT

echo "Starting VoiceKAM on http://127.0.0.1:${PORT} ..."
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "Local URL ready: http://127.0.0.1:${PORT}"

if [[ "${1:-}" == "--local" ]]; then
  wait "$SERVER_PID"
  exit 0
fi

if command -v cloudflared >/dev/null 2>&1; then
  echo "Starting free Cloudflare Quick Tunnel ..."
  cloudflared tunnel --url "http://127.0.0.1:${PORT}" --protocol http2 --no-autoupdate 2>&1 | tee /dev/stderr | grep --line-buffered -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -n 1 | xargs -r -I{} printf "\nPUBLIC HTTPS LIVE DEMO URL: %s\n\n" "{}" &
  TUNNEL_PID=$!
  wait "$SERVER_PID"
else
  echo "cloudflared not found. Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  echo "Running locally on http://127.0.0.1:${PORT} (Ctrl+C to stop)."
  wait "$SERVER_PID"
fi

