#!/usr/bin/env bash
# install.sh — install PillPanel (system deps + pip package + autostart entry)
#
# Usage:
#   ./install.sh           install from this local repo
#   ./install.sh remove    remove autostart entry (pip uninstall separately)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART="$HOME/.config/autostart/pillpanel.desktop"

# ── Remove ─────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "remove" ]]; then
    rm -f "$AUTOSTART" "$HOME/.local/share/applications/pillpanel.desktop"
    echo "Autostart and app menu entries removed."
    echo "To fully uninstall: pip3 uninstall pillpanel"
    exit 0
fi

# ── System dependencies ────────────────────────────────────────────────────────
sudo apt-get install -y \
    python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 \
    gir1.2-xapp-1.0 gir1.2-wnck-3.0 \
    python3-dbus

# ── Install Python package ─────────────────────────────────────────────────────
pip3 install --user --break-system-packages "$(dirname "$0")"

# ── App menu entry ────────────────────────────────────────────────────────────
mkdir -p "$HOME/.local/share/applications"
cp "$REPO_DIR/pillpanel.desktop" "$HOME/.local/share/applications/pillpanel.desktop"

# ── Autostart ─────────────────────────────────────────────────────────────────
mkdir -p "$HOME/.config/autostart"
cat > "$AUTOSTART" << EOF
[Desktop Entry]
Name=PillPanel
Comment=Custom Cinnamon top panel
Exec=$HOME/.local/bin/pillpanel
Type=Application
X-GNOME-Autostart-enabled=true
EOF

echo ""
echo "PillPanel installed."
echo "  Command  : pillpanel  (make sure ~/.local/bin is on your PATH)"
echo "  Autostart: $AUTOSTART"
echo ""
echo "To install from GitHub instead:"
echo "  pip3 install --user --break-system-packages git+https://github.com/NoeGarCou/pillpanel.git"
