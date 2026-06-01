#!/usr/bin/env python3
"""
pillpanel.py — PillPanel, a custom top panel for Linux Mint Cinnamon.

Replaces Cinnamon's built-in top panel. Features:
  • Floating pill design — transparent outer window + dark rounded inner pill
  • Python applet plugin system — each section is an independent Applet class
  • SNI + XApp system tray (blueman, ClickUp, mintUpdate, nvidia-prime, …)
  • Volume control (PipeWire / PulseAudio via pactl)
  • Network status (NetworkManager D-Bus)
  • Battery indicator (UPower D-Bus)
  • Clock + calendar popover
  • Application launcher with search
  • Show Desktop button

Layout:
  [AppMenu]         [Date  Time]         [Bat][Net][Vol] [Tray…] [Desktop]
     left              center                        right

Usage:
    python3 pillpanel.py [--debug]

ROLLBACK (restore Cinnamon panel if something goes wrong):
    pkill -f pillpanel.py && cinnamon --replace &
"""

import sys
import json
import os
import argparse
import logging

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('XApp', '1.0')
gi.require_version('Wnck', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo

# D-Bus main loop MUST be set before any bus connection is opened
import dbus
import dbus.service
import dbus.mainloop.glib
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

# ─── Config ────────────────────────────────────────────────────────────────────

_CONFIG_PATH = os.path.expanduser('~/.config/pillpanel/config.json')

_CONFIG_DEFAULTS = {
    'pill_bg':              'rgba(30, 30, 36, 0.863)',
    'pill_stroke':          'rgba(255, 255, 255, 0.039)',
    'outer_height':         36,
    'icon_size':            16,
    'show_desktop_icon_size': 16,
    'appmenu_btn_icon_size':  16,
    'appmenu_icon_size':      20,
    'appmenu_font_size':      13,
}
# pill_radius is intentionally absent — always auto-computed from outer_height.

def load_config() -> dict:
    cfg = dict(_CONFIG_DEFAULTS)
    try:
        with open(_CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.getLogger('pillpanel').warning(f"[Config] Load failed: {e}")
    return cfg

def save_config(cfg: dict):
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

# ─── Constants ─────────────────────────────────────────────────────────────────

OUTER_HEIGHT     = 36    # transparent outer window height (px)
PILL_RADIUS      = 14    # inner pill border-radius (px)
PILL_MARGIN_TOP  =  4    # gap from outer top edge to pill
PILL_MARGIN_SIDE =  8    # gap from outer side edges to pill
PILL_BG          = "rgba(30, 30, 36, 0.863)"
PILL_STROKE      = "rgba(255, 255, 255, 0.039)"

# ─── CSS ───────────────────────────────────────────────────────────────────────

def _make_panel_css(pill_bg: str, pill_stroke: str, outer_height: int) -> str:
    """Generate the full panel CSS from config values.

    pill_radius is auto-computed as half the visible pill height so the
    pill always has a true rounded-capsule shape regardless of height.
    """
    pill_radius = (outer_height - PILL_MARGIN_TOP * 2) // 2
    btn_min_h   = outer_height - PILL_MARGIN_TOP * 2 - 4
    popup_bg    = _popup_bg(pill_bg)
    return f"""
/* ── Pill container ───────────────────────────────────────── */
.pill {{
    background-color: {pill_bg};
    border-radius: {pill_radius}px;
    border: 1px solid {pill_stroke};
    padding: 0 6px 0 0;
}}

/* ── Generic panel button (menu, clock, volume icon, etc.) ── */
.panel-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    color: white;
    font-size: 13px;
    padding: 2px 6px;
    min-height: {btn_min_h}px;
    outline: none;
    border-radius: 100px;
}}
.panel-btn:hover {{
    background-color: rgba(255,255,255,0.12);
    border-radius: 100px;
}}
.panel-btn:active {{
    background-color: rgba(255,255,255,0.20);
    border-radius: 100px;
}}

/* ── AppMenu button — flush with pill left edge ─────────── */
.appmenu-btn {{
    padding-left: 5px;
    padding-right: 6px;
}}

/* ── Tray icon buttons (smaller, square) ─────────────────── */
.tray-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 1px 2px;
    min-width: 24px;
    min-height: 24px;
    outline: none;
    border-radius: 100px;
}}
.tray-btn:hover {{
    background-color: rgba(255,255,255,0.10);
    border-radius: 100px;
}}

/* ── Text labels inside the pill ────────────────────────── */
.panel-label {{
    color: white;
    font-size: 12px;
}}

/* ── Popovers ─────────────────────────────────────────────
   GTK3 popover chrome is drawn by the theme; we only style
   the content box that we add inside each popover.         */
.popover-box {{
    padding: 10px;
}}

/* ── Battery level bar ───────────────────────────────────────── */
.bat-level trough {{
    background: rgba(255,255,255,0.15);
    border-radius: 3px;
    min-height: 8px;
}}
.bat-level block.filled {{
    background: #1565c0;
    border-radius: 3px;
    border: none;
}}
.bat-level block.low, .bat-level block.indicator {{
    background: #c62828;
    border-radius: 3px;
    border: none;
}}

/* ── Volume slider ───────────────────────────────────────────── */
.vol-slider trough {{
    background: rgba(255,255,255,0.18);
    border-radius: 3px;
    min-height: 4px;
}}
.vol-slider trough highlight {{
    background: rgba(255,255,255,0.85);
    border-radius: 3px;
}}
.vol-slider slider {{
    background: white;
    border-radius: 100px;
    border: none;
    box-shadow: none;
    min-width: 14px;
    min-height: 14px;
    margin: -5px 0;
}}
.vol-slider slider:hover {{
    background: rgba(255,255,255,0.90);
}}
.vol-slider value {{
    color: white;
    font-size: 11px;
}}

/* ── Network popup AP rows ──────────────────────────────────── */
.net-row-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 0;
    border-radius: 6px;
    outline: none;
}}
.net-row-btn:hover {{
    background: rgba(255,255,255,0.08);
}}

/* ── Popup windows ──────────────────────────────────────────── */
window.panel-popup {{
    background-color: {popup_bg};
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.07);
}}

/* ── Calendar events area ────────────────────────────────────── */
.cal-events-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    outline: none;
    border-radius: 8px;
    padding: 0;
}}
.cal-events-btn:hover {{
    background: rgba(255,255,255,0.06);
}}
.cal-event-dot {{
    color: #5294e2;
    font-size: 10px;
}}
.cal-event-time {{
    color: rgba(255,255,255,0.55);
    font-size: 11px;
}}
.cal-event-summary {{
    color: white;
    font-size: 12px;
}}

/* ── Calendar ────────────────────────────────────────────────── */
separator.cal-sep {{
    background-color: rgba(255,255,255,0.15);
    min-height: 1px;
    margin: 4px 0;
}}
.cal-dow {{
    color: rgba(255,255,255,0.60);
    font-size: 12px;
}}
.cal-weekend {{
    color: white;
    font-weight: bold;
}}
.cal-day-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    color: white;
    font-size: 13px;
    padding: 0;
    min-width: 36px;
    min-height: 36px;
    border-radius: 100px;
    outline: none;
}}
.cal-day-btn:hover {{
    background: rgba(255,255,255,0.12);
}}
.cal-today {{
    background-color: #1565c0;
    color: white;
    font-weight: bold;
    border-radius: 100px;
}}
.cal-today:hover {{
    background-color: #1976d2;
}}
.cal-other-month {{
    color: rgba(255,255,255,0.28);
}}
.cal-selected {{
    background-color: rgba(255,255,255,0.18);
    border-radius: 100px;
}}
.cal-nav-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    color: rgba(255,255,255,0.65);
    font-size: 11px;
    padding: 2px 6px;
    min-width: 22px;
    min-height: 24px;
    border-radius: 100px;
    outline: none;
}}
.cal-nav-btn:hover {{
    background: rgba(255,255,255,0.12);
    color: white;
}}
.cal-nav-label {{
    color: white;
    font-size: 13px;
    font-weight: bold;
}}
.cal-dim {{
    color: rgba(255,255,255,0.40);
}}
.cal-settings-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    color: rgba(255,255,255,0.60);
    font-size: 12px;
    outline: none;
    border-radius: 6px;
    padding: 2px 6px;
}}
.cal-settings-btn:hover {{
    color: white;
    background: rgba(255,255,255,0.08);
}}

/* ── App launcher grid ───────────────────────────────────── */
.app-btn {{
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 4px;
    border-radius: 100px;
    outline: none;
}}
.app-btn:hover {{
    background: rgba(255,255,255,0.12);
    border-radius: 100px;
}}
.app-label {{
    color: rgba(255,255,255,0.90);
    font-size: 11px;
}}
"""


def _popup_bg(pill_bg: str) -> str:
    """Return a near-opaque version of the pill colour for popup backgrounds."""
    import re
    m = re.match(r'rgba\((\d+),\s*(\d+),\s*(\d+)', pill_bg)
    if m:
        return f'rgba({m.group(1)}, {m.group(2)}, {m.group(3)}, 0.98)'
    return 'rgba(30, 30, 36, 0.98)'


# ─── Logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger('pillpanel')


def _setup_logging(debug: bool):
    fmt = "%(asctime)s.%(msecs)03d  %(levelname)-7s  %(message)s"
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format=fmt, datefmt="%H:%M:%S",
    )


_BANNER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                        PillPanel — custom Cinnamon panel                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  ROLLBACK:   pkill -f pillpanel.py && cinnamon --replace &                   ║
║  Also kill:  pkill xapp-sn-watcher   (before first run)                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# ═══════════════════════════════════════════════════════════════════════════════
# PillWindow — two-layer GTK window
# ═══════════════════════════════════════════════════════════════════════════════

class PillWindow:
    """
    Layer 1 — Outer window (transparent):
        Full-screen-width DOCK-type Gtk.Window. Stays above all other windows.
        Transparent so only the pill is visible, but mouse events in the margin
        still reach our process — this fixes the "cursor gap" issue Cinnamon's
        panel has with top margins.

    Layer 2 — Inner pill:
        A Gtk.Box styled with CSS: dark background + rounded corners.
        Three sub-sections (left / center / right) host the applets.
    """

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._css_provider = None
        self._build()

    def _build(self):
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("PillPanel")
        win.set_type_hint(Gdk.WindowTypeHint.DOCK)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.stick()
        win.set_keep_above(True)
        win.set_decorated(False)

        # RGBA visual for outer window transparency
        screen = win.get_screen()
        rgba   = screen.get_rgba_visual()
        if rgba and screen.is_composited():
            win.set_visual(rgba)
            log.info("[Window] RGBA visual — compositor active")
        else:
            log.warning("[Window] No RGBA visual — transparency unavailable")
        win.set_app_paintable(True)

        # Span the primary monitor
        display  = Gdk.Display.get_default()
        monitor  = display.get_primary_monitor()
        geo      = monitor.get_geometry()
        height = self._config.get('outer_height', OUTER_HEIGHT)
        bg     = self._config.get('pill_bg',      PILL_BG)
        stroke = self._config.get('pill_stroke',  PILL_STROKE)

        win.resize(geo.width, height)
        win.move(geo.x, geo.y)
        log.info(f"[Window] {geo.width}×{height} at ({geo.x},{geo.y})")

        win.connect('draw',    self._draw_transparent)
        win.connect('destroy', Gtk.main_quit)

        # Apply CSS — stored so reload_css() can update it in-place
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(
            _make_panel_css(bg, stroke, height).encode()
        )
        Gtk.StyleContext.add_provider_for_screen(
            screen, self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ── Pill layout ────────────────────────────────────────────────────────
        outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.pill.get_style_context().add_class('pill')
        self.pill.set_margin_top(PILL_MARGIN_TOP)
        self.pill.set_margin_start(PILL_MARGIN_SIDE)
        self.pill.set_margin_end(PILL_MARGIN_SIDE)

        # Three sections — center is always centred regardless of left/right sizes
        self.left   = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.center = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.right  = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)

        self.pill.pack_start(self.left,  False, False, 0)
        self.pill.set_center_widget(self.center)
        self.pill.pack_end(self.right,   False, False, 0)

        outer_box.pack_start(self.pill, True, True, 0)

        # ── Revealer — wraps the content for auto-hide slide animation ─────────
        # The outer window NEVER moves (avoids sliding onto monitors above).
        # The Revealer animates the pill in/out inside the fixed transparent window.
        # The transparent window area is always the cursor hot-zone.
        self._revealer = Gtk.Revealer()
        self._revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._revealer.set_transition_duration(200)
        self._revealer.set_valign(Gtk.Align.START)
        self._revealer.set_reveal_child(True)
        self._revealer.add(outer_box)

        # Overlay: spacer holds the window at OUTER_HEIGHT when revealer collapses
        overlay = Gtk.Overlay()
        spacer  = Gtk.Box()
        spacer.set_size_request(geo.width, height)
        overlay.add(spacer)
        overlay.add_overlay(self._revealer)

        win.add(overlay)
        self.window = win

    @staticmethod
    def _draw_transparent(widget, cr):
        """Paint the outer window fully transparent, leaving only the pill visible."""
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False  # let GTK draw children (the pill) on top

    def add_applet(self, widget, section: str):
        """Place a widget into 'left', 'center', or 'right'."""
        target = {'left': self.left, 'center': self.center, 'right': self.right}[section]
        target.pack_start(widget, False, False, 0)

    def reveal(self):
        """Slide the pill into view (called by AutoHideManager)."""
        self._revealer.set_reveal_child(True)
        # Restore full input capture so the panel intercepts clicks again.
        self.window.input_shape_combine_region(None)

    def conceal(self):
        """Slide the pill out of view (called by AutoHideManager)."""
        self._revealer.set_reveal_child(False)
        # Empty input shape → window is fully click-through while hidden.
        # Without this the transparent outer window blocks clicks on whatever
        # is underneath (e.g. the close button of a maximised window).
        self.window.input_shape_combine_region(cairo.Region())

    def show(self):
        self.window.show_all()


# ═══════════════════════════════════════════════════════════════════════════════
# PillPanel — loads and manages all applets
# ═══════════════════════════════════════════════════════════════════════════════

class PillPanel:
    """
    Instantiates every applet and wires their widgets into the PillWindow.
    Provides shared resources (D-Bus session bus, system bus, debug flag)
    to all applets via self.

    Applet load order within each section = visual order left-to-right.
    """

    def __init__(self, debug: bool = False, autohide: bool = False):
        self.debug      = debug
        self.config     = load_config()
        self.bus        = dbus.SessionBus()
        self.system_bus = dbus.SystemBus()
        self.window     = PillWindow(self.config)   # config drives height/colours
        self._applets   = []
        self.autohide   = None

        if autohide:
            from applets.autohide import AutoHideManager
            geo = Gdk.Display.get_default().get_primary_monitor().get_geometry()
            self.autohide = AutoHideManager(
                win     = self.window.window,
                height  = self.config.get('outer_height', OUTER_HEIGHT),
                mon_x   = geo.x,
                mon_y   = geo.y,
                mon_w   = geo.width,
                smart   = True,
                on_show = self.window.reveal,
                on_hide = self.window.conceal,
            )
            log.info("[Panel] Auto-hide enabled (smart mode)")

        self._load_applets()

    def reload_css(self):
        """Regenerate and hot-swap the full CSS from self.config."""
        cfg = self.config
        css = _make_panel_css(
            cfg.get('pill_bg',      PILL_BG),
            cfg.get('pill_stroke',  PILL_STROKE),
            cfg.get('outer_height', OUTER_HEIGHT),
        )
        self.window._css_provider.load_from_data(css.encode())
        log.debug("[Panel] CSS reloaded")

    def _load_applets(self):
        # Import here so D-Bus mainloop is already set
        from applets.appmenu import AppMenuApplet
        from applets.clock       import ClockApplet
        from applets.battery     import BatteryApplet
        from applets.network     import NetworkApplet
        from applets.volume      import VolumeApplet
        from applets.tray        import TrayApplet
        from applets.showdesktop import ShowDesktopApplet

        specs = [
            # (class,              section)
            (AppMenuApplet,     'left'),
            (ClockApplet,       'center'),
            (BatteryApplet,     'right'),
            (NetworkApplet,     'right'),
            (VolumeApplet,      'right'),
            (TrayApplet,        'right'),
            (ShowDesktopApplet, 'right'),
        ]

        for AppletClass, section in specs:
            try:
                applet = AppletClass(self)
                widget = applet.build()
                self.window.add_applet(widget, section)
                self._applets.append(applet)
                log.info(f"[Panel] {AppletClass.__name__} → {section}")
            except SystemExit:
                raise  # propagate intentional exits (e.g. Watcher name conflict)
            except Exception as e:
                log.error(
                    f"[Panel] {AppletClass.__name__} failed to load: {e}",
                    exc_info=self.debug,
                )

        self._apply_icon_size()

    def _apply_icon_size(self):
        """Set pixel_size on every Gtk.Image in the pill to match config."""
        px = self.config.get('icon_size', 16)
        def walk(widget):
            if isinstance(widget, Gtk.Image):
                widget.set_pixel_size(px)
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    walk(child)
        walk(self.window.pill)
        # Let applets with per-icon custom sizes override the global walk
        for applet in self._applets:
            if hasattr(applet, 'after_icon_size'):
                applet.after_icon_size()
        log.debug(f"[Panel] Icon size applied: {px}px")

    def run(self):
        import signal as _signal
        _signal.signal(_signal.SIGINT, lambda *_: Gtk.main_quit())
        self.window.show()

        if self.autohide:
            from applets.popup import register_autohide
            register_autohide(self.autohide)
            self.autohide.start()

        log.info("[Panel] Running. Ctrl+C or close window to quit.")
        Gtk.main()
        for applet in self._applets:
            try:
                applet.destroy()
            except Exception:
                pass
        log.info("[Panel] Exited cleanly.")


# ─── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PillPanel — custom Cinnamon top panel",
    )
    parser.add_argument('--debug', action='store_true',
                        help="Verbose D-Bus and GTK debug output")
    args = parser.parse_args()
    _setup_logging(args.debug)
    print(_BANNER)
    PillPanel(debug=args.debug, autohide=True).run()


if __name__ == '__main__':
    main()
