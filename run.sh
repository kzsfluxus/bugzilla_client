#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
  echo ">>> Creating virtual environment"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ -f ".secrets" ]; then
  echo ">>> Loading secrets from .secrets"
  set -a
  # shellcheck disable=SC1091
  . ./.secrets
  set +a
fi

echo ">>> Installing/updating requirements"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt >/dev/null

MODE="${1:-tui}"
if [ "$MODE" = "tui" ]; then
  shift || true
  echo ">>> Starting Textual TUI"
  python app.py "$@"
else
  echo ">>> Starting CLI"
  python cli.py "$@"
fi
