"""
applets/popup.py — manually positioned popup window for panel applets.

Also manages auto-hide pinning: whenever a popup is open the panel must
not slide away. Call register_autohide(mgr) once at startup so every
PanelPopup automatically pins/unpins the AutoHideManager.

Gtk.Popover is unreliable inside DOCK-type windows: GTK may cache the
relative widget's allocation before the panel is laid out, placing the
popup at (0,0) instead of below the button.

PanelPopup uses a Gtk.Window(POPUP) instead, computing position from
get_root_coords() at the moment show() is called — after the panel is
fully realized and positioned on screen.

Dismiss-on-outside-click is implemented via a GDK seat grab.
POPUP windows never receive keyboard focus, so focus-out-event never
fires on X11. Instead, when the popup is shown we grab pointer +
keyboard (owner_events=True so in-app clicks are delivered normally).
Any click outside the application is then redirected to the popup
window, where _on_button_press detects it's outside the popup rect and
calls hide(). The grab is released when hide() runs.

Only one popup is open at a time (_active_popup). Opening a second
popup implicitly closes the first.
"""

import logging

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib

log = logging.getLogger('pillpanel.popup')

# Module-level reference set by pillpanel after AutoHideManager is created.
_autohide = None

# At most one popup is open at a time.
_active_popup = None


def register_autohide(mgr):
    """Called once from pillpanel.py to connect the auto-hide manager."""
    global _autohide
    _autohide = mgr


class PanelPopup:
    """
    Lightweight dropdown popup for panel applets.

    Positioned below the trigger button with a small gap.
    Closes on Escape key or any click outside the popup.

    Usage:
        popup = PanelPopup(my_box_widget)
        btn.connect('clicked', lambda b: popup.toggle(b))
    """

    GAP = 6  # px gap between panel bottom and popup top

    def __init__(self, content: Gtk.Widget, center: bool = False):
        self._visible = False
        self._center  = center

        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_type_hint(Gdk.WindowTypeHint.DROPDOWN_MENU)
        win.get_style_context().add_class('panel-popup')

        # Request RGBA visual so the GTK theme can composite rounded corners.
        screen = win.get_screen()
        rgba = screen.get_rgba_visual()
        if rgba and screen.is_composited():
            win.set_visual(rgba)
        win.set_app_paintable(True)

        win.add(content)
        content.show_all()

        win.connect('key-press-event',   self._on_key)
        win.connect('button-press-event', self._on_button_press)

        self._win = win

    # ── Public API ─────────────────────────────────────────────────────────────

    def toggle(self, btn: Gtk.Widget):
        if self._visible:
            self.hide()
        else:
            self.show(btn)

    def show(self, btn: Gtk.Widget):
        """
        Show the popup below `btn`.
        Position is computed from the button's root (screen) coordinates,
        so the button must already be realized and on screen.
        """
        global _active_popup

        # Close any other open popup first
        if _active_popup and _active_popup is not self:
            _active_popup.hide()

        gdk_win = btn.get_window()
        if not gdk_win:
            return

        alloc     = btn.get_allocation()
        rx, ry    = gdk_win.get_root_coords(alloc.x, alloc.y)

        x = int(rx)
        y = int(ry) + alloc.height + self.GAP

        display  = Gdk.Display.get_default()
        monitor  = display.get_primary_monitor()
        screen_w = monitor.get_geometry().width
        _, nat_w = self._win.get_preferred_width()

        if self._center:
            x = max(4, (screen_w - nat_w) // 2)
        else:
            x = min(x, screen_w - nat_w - 4)
            x = max(x, 4)

        # Move before show so the window never flashes at (0,0)
        self._win.move(x, y)
        self._win.show_all()
        self._visible  = True
        _active_popup  = self

        if _autohide:
            _autohide.pin()

        # Grab pointer + keyboard so outside clicks are delivered to us
        self._acquire_grab()

    def hide(self):
        global _active_popup
        self._win.hide()
        self._visible = False
        if _active_popup is self:
            _active_popup = None
        self._release_grab()
        if _autohide:
            _autohide.unpin()

    # ── Grab management ────────────────────────────────────────────────────────

    def _acquire_grab(self):
        gdk_win = self._win.get_window()
        if not gdk_win:
            return
        seat = Gdk.Display.get_default().get_default_seat()
        caps = Gdk.SeatCapabilities.POINTER | Gdk.SeatCapabilities.KEYBOARD
        # owner_events=True: in-app events still go to their target widgets;
        # events outside the app are redirected to gdk_win (the popup).
        status = seat.grab(gdk_win, caps, True, None, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS:
            log.warning(f"[Popup] Seat grab failed: {status.value_nick}")

    def _release_grab(self):
        Gdk.Display.get_default().get_default_seat().ungrab()

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_key(self, _win, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _on_button_press(self, _win, event):
        """
        With the seat grab active, clicks outside the application are
        delivered here. Verify the click is truly outside the popup rect
        (guards against unhandled clicks that bubble up from popup widgets)
        then close.
        """
        if not self._visible:
            return False
        wx, wy = self._win.get_position()
        w, h   = self._win.get_size()
        if not (wx <= event.x_root < wx + w and wy <= event.y_root < wy + h):
            self.hide()
            return True  # consume — don't let the click fall through to desktop
        return False
