#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! pacman -Q tk >/dev/null 2>&1; then
  echo "Missing dependency: tk"
  echo "Install with: sudo pacman -Syu tk"
  exit 1
fi

exec python3 soundpad_app.py
