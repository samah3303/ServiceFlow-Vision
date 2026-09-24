#!/bin/zsh
set -e

APP_DIR=${0:A:h}
cd "$APP_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "Preparing Restaurant Vision for its first run…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python server.py --open

