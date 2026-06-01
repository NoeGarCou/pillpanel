# PillPanel

A custom floating top panel for **Linux Mint Cinnamon**, written in Python + GTK 3.

Replaces Cinnamon's built-in panel with a pill-shaped bar that floats above your desktop. Smart auto-hide keeps it out of the way until you need it.

---

## Features

- **Floating pill design** — transparent outer window with a dark rounded inner pill
- **System tray** — hosts SNI / AppIndicator icons (network manager, Bluetooth, volume, ClickUp, etc.) via a built-in StatusNotifierWatcher
- **Volume** — scroll or click to adjust; PipeWire / PulseAudio via `pactl`
- **Network** — NetworkManager status and AP list
- **Battery** — UPower charge level and status
- **Clock** — date/time with a calendar popover
- **App launcher** — system menu with curated shortcuts and a Preferences window
- **Show Desktop** — minimises all windows
- **Smart auto-hide** — hides when a window overlaps the panel zone, reveals when the cursor reaches the top edge

---

## Requirements

- Linux Mint 22 (Cinnamon, Ubuntu 24.04 base) — other Ubuntu-based distros may work
- X11 (Wayland is not supported)
- Python 3.10+

---

## Installation

### From GitHub (recommended)

```bash
# 1. Install system dependencies
sudo apt-get install -y \
    python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 \
    gir1.2-xapp-1.0 gir1.2-wnck-3.0 \
    python3-dbus

# 2. Install the package
# (--break-system-packages is required on Ubuntu 24.04+; safe with --user)
pip3 install --user --break-system-packages git+https://github.com/NoeGarCou/NPanel.git
```

Make sure `~/.local/bin` is on your `PATH` (it usually is on Mint; if not, add `export PATH="$HOME/.local/bin:$PATH"` to your `~/.bashrc`).

### From a local clone

```bash
git clone https://github.com/NoeGarCou/NPanel.git
cd NPanel
./install.sh       # installs deps + pip package + autostart entry
```

`./install.sh remove` removes the autostart entry. `pip3 uninstall pillpanel` removes the package.

---

## Running

```bash
pillpanel           # normal
pillpanel --debug   # verbose D-Bus and GTK output
```

### Autostart on login

`./install.sh` creates `~/.config/autostart/pillpanel.desktop` automatically. To set it up manually:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/pillpanel.desktop << EOF
[Desktop Entry]
Name=PillPanel
Exec=$HOME/.local/bin/pillpanel
Type=Application
X-GNOME-Autostart-enabled=true
EOF
```

### Disabling Cinnamon's panel first

PillPanel claims the SNI StatusNotifierWatcher D-Bus name. If Cinnamon's panel is still running, the tray icons won't transfer. Disable Cinnamon's panel via **Panel Settings → Remove this panel** before starting PillPanel.

**Rollback** if something goes wrong:
```bash
pkill -f pillpanel.py && cinnamon --replace &
```

---

## Preferences

Open the app menu (Linux Mint logo, top-left) → **Panel Preferences**.

| Setting | Description |
|---|---|
| Background colour | Pill fill colour (RGBA, applied live) |
| Border colour | Pill stroke colour (RGBA, applied live) |
| Panel height | Outer window height in px (requires restart) |
| Icon size | General icon size for panel buttons (requires restart) |
| Show Desktop icon | Size of the Show Desktop icon (requires restart) |
| App menu icon size | Icon size inside the app menu (requires restart) |
| App menu font size | Font size inside the app menu (requires restart) |

Config is saved to `~/.config/pillpanel/config.json`.

---

## Uninstalling

```bash
pip3 uninstall pillpanel
rm -f ~/.config/autostart/pillpanel.desktop
```
