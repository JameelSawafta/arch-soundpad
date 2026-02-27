#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_BIN="$BIN_DIR/arch-soundpad"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/arch-soundpad.desktop"

mkdir -p "$BIN_DIR" "$DESKTOP_DIR"

cat > "$APP_BIN" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$APP_DIR"
exec python3 soundpad_app.py
EOF
chmod +x "$APP_BIN"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Arch Soundpad
Comment=Play clips into a virtual microphone
Exec=$APP_BIN
Icon=audio-card
Terminal=false
Categories=AudioVideo;Audio;
StartupNotify=true
EOF

update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true

echo "Installed launcher: $APP_BIN"
echo "Desktop entry: $DESKTOP_FILE"
