#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/4] Python syntax check"
python3 -m py_compile soundpad_app.py

echo "[2/4] Headless application check"
python3 soundpad_app.py --headless-check

echo "[3/4] Shell script syntax check"
bash -n run.sh install.sh arch-soundpad

echo "[4/4] Regenerate .SRCINFO"
makepkg --printsrcinfo > .SRCINFO

echo "Smoke test completed successfully"
