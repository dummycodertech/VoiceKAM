#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-python3}"
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"
fi

PROJECT_NAME="$("$PY" -c "from app.config import PROJECT_NAME; print(PROJECT_NAME)")"
TARGET_DIR="${1:-../${PROJECT_NAME}-clean}"

if [[ -e "$TARGET_DIR" ]]; then
  echo "Error: destination '$TARGET_DIR' already exists. Remove it or pass another path." >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
git archive HEAD | tar -x -C "$TARGET_DIR"

(
  cd "$TARGET_DIR"
  git init
  git add -A
  git commit -m "Initial commit: ${PROJECT_NAME}"
)

echo "Clean repository exported with single initial commit at: $TARGET_DIR"
