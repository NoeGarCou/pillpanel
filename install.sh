#!/usr/bin/env bash
# install.sh — install PillPanel for the current user.
#
# Usage:
#   ./install.sh          install (or reinstall)
#   ./install.sh remove   remove launcher and autostart entry
#
# The script installs no files outside the user's home directory
# (apart from system packages via apt when missing).
# Run from the repo root — the launcher will exec python3 from this directory.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="$HOME/.local/bin/pillpanel"
AUTOSTART="$HOME/.config/autostart/pillpanel.desktop"

# ── Remove ─────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "remove" ]]; then
    rm -f "$LAUNCHER" "$AUTOSTART"
    echo "PillPanel removed (launcher and autostart entry deleted)."
    echo "To stop a running instance: pkill -f pillpanel.py"
    exit 0
fi

# ── Check / install system dependencies ───────────────────────────────────────
DEPS=(
    python3
    python3-gi
    python3-gi-cairo
    gir1.2-gtk-3.0
    gir1.2-gdkpixbuf-2.0
    gir1.2-xapp-1.0
    gir1.2-wnck-3.0
    python3-dbus
    libcairo2-dev
)

missing=()
for dep in "${DEPS[@]}"; do
    dpkg -s "$dep" &>/dev/null || missing+=("$dep")
done

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Installing missing packages: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
fi

# ── Launcher ──────────────────────────────────────────────────────────────────
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCHER" << EOF
#!/usr/bin/env bash
exec python3 "$REPO_DIR/pillpanel.py" "\$@"
EOF
chmod +x "$LAUNCHER"

# ── Autostart ─────────────────────────────────────────────────────────────────
mkdir -p "$HOME/.config/autostart"
cat > "$AUTOSTART" << EOF
[Desktop Entry]
Name=PillPanel
Comment=Custom Cinnamon top panel
Exec=$LAUNCHER
Type=Application
X-GNOME-Autostart-enabled=true
EOF

echo ""
echo "PillPanel installed."
echo "  Launcher : $LAUNCHER"
echo "  Autostart: $AUTOSTART"
echo ""
echo "Make sure ~/.local/bin is on your PATH, then run: pillpanel"
echo "To uninstall: ./install.sh remove"
