"""
applets/autohide.py — smart auto-hide manager for PillPanel.

The outer window NEVER moves (fixes the multi-monitor teleport bug).
A Gtk.Revealer inside the window slides the pill in/out instead.

State machine
─────────────
  _forced_visible  Set when cursor reaches ≤ REVEAL_PX from screen top.
                   Cleared only when cursor leaves the outer window entirely.
                   Overrides EVERYTHING — panel cannot hide in this state.

  _pins            >0 while a popup is open.
                   Panel is pinned visible; overlap check is suppressed.

  _hidden          True when the revealer is currently concealing the pill.

Transitions
───────────
  Cursor reaches top edge  → _forced_visible=True  → reveal (always)
  Cursor leaves panel zone → _forced_visible=False → re-evaluate
  Window overlaps panel   → hide  (only if not forced_visible and pins==0)
  No window overlap       → reveal (only if smart mode and pins==0)
  pin()                   → reveal and suspend hiding
  unpin()                 → re-evaluate after a short debounce

Enter/leave on the outer window are only wired in non-smart mode to
implement classic auto-hide (always hide on cursor leave).
"""

import logging

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Wnck', '3.0')
from gi.repository import Gtk, Gdk, GLib, Wnck

log = logging.getLogger('pillpanel.autohide')


class AutoHideManager:

    REVEAL_PX       =  2    # px from screen top that trigger forced-visible
    CURSOR_MS       = 60    # cursor-edge poll interval
    WINDOW_MS       = 350   # window-overlap poll interval
    HIDE_DELAY_MS   = 600   # grace period before hiding (non-smart mode)
    UNPIN_DELAY_MS  = 150   # debounce after popup closes before re-evaluating

    def __init__(self, win: Gtk.Window, height: int,
                 mon_x: int, mon_y: int, mon_w: int,
                 smart: bool, on_show, on_hide):
        self._win     = win
        self._height  = height
        self._mon_x   = mon_x
        self._mon_y   = mon_y
        self._mon_w   = mon_w
        self._smart   = smart
        self._on_show = on_show
        self._on_hide = on_hide

        self._pins           = 0
        self._hidden         = False
        self._forced_visible = False   # cursor is at screen top edge
        self._hide_id        = None

        # In non-smart mode, enter/leave drive the hide/show cycle.
        # In smart mode they are NOT connected: window overlap drives it instead,
        # and showing from the cursor area would defeat the smart-hide purpose
        # (e.g. cursor on the close button of a maximised window would reveal
        # the panel and obstruct it).
        if not smart:
            win.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK |
                Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            win.connect('enter-notify-event', self._on_enter)
            win.connect('leave-notify-event', self._on_leave)

    def start(self):
        GLib.timeout_add(self.CURSOR_MS,  self._poll_cursor)
        GLib.timeout_add(self.WINDOW_MS,  self._poll_windows)
        log.info(f"[AutoHide] started  smart={self._smart}")

    # ── Pin API (used by PanelPopup) ───────────────────────────────────────────

    def pin(self):
        """Keep panel visible. Every pin() must be matched by an unpin()."""
        self._pins += 1
        self._cancel_hide()
        self._show()

    def unpin(self):
        self._pins = max(0, self._pins - 1)
        if self._pins == 0:
            # Brief debounce: avoids a flash-hide the instant a popup closes
            GLib.timeout_add(self.UNPIN_DELAY_MS, self._deferred_evaluate)

    def _deferred_evaluate(self):
        if self._pins == 0:
            self._evaluate()
        return False

    # ── Classic auto-hide enter / leave (non-smart mode only) ─────────────────

    def _on_enter(self, widget, event):
        # Should never fire in smart mode, but guard defensively in case
        # GTK delivers a synthetic enter via a child widget on X11.
        if self._smart:
            return
        if event.detail == Gdk.NotifyType.INFERIOR:
            return
        self.pin()

    def _on_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return
        self.unpin()

    # ── Polling ────────────────────────────────────────────────────────────────

    def _poll_cursor(self):
        """
        Detect when cursor reaches the very top of the screen and force-reveal.
        This is the only way to show the panel when a window is covering it.
        """
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            _, cx, cy = seat.get_pointer().get_position()
            at_edge  = (cy <= self._mon_y + self.REVEAL_PX)
            # Hysteresis: once forced-visible, stay locked until cursor leaves
            # the entire outer window, not just the 2 px trigger strip.
            # This stops _poll_cursor and _poll_windows from fighting each other
            # while the cursor hovers anywhere inside the 36 px panel zone.
            in_panel = (cy <= self._mon_y + self._height)

            if at_edge and not self._forced_visible:
                self._forced_visible = True
                self._show()
            elif self._forced_visible and not in_panel:
                # Cursor left the outer window entirely — release the edge lock
                self._forced_visible = False
                if self._pins == 0:
                    self._evaluate()
        except Exception as e:
            log.debug(f"[AutoHide] cursor poll: {e}")
        return True

    def _poll_windows(self):
        """Periodically check window overlap (smart mode logic)."""
        # Both cursor-at-edge and open popups suppress the overlap check
        if self._forced_visible or self._pins > 0:
            return True
        self._evaluate()
        return True

    def _evaluate(self):
        """Decide whether to show or hide based on current state."""
        if self._forced_visible or self._pins > 0:
            return
        if self._smart:
            if self._overlapping_window_exists():
                self._hide()
            else:
                self._cancel_hide()
                self._show()
        else:
            # Classic auto-hide: schedule a hide when unpinned
            self._schedule_hide()

    # ── Window overlap detection ───────────────────────────────────────────────

    def _overlapping_window_exists(self) -> bool:
        """
        True if any non-minimised, non-desktop/dock window on this monitor
        should trigger a panel hide.

        Two triggers:
        1. Maximised-vertically or fullscreen state — checked regardless of
           geometry, because our DOCK type causes the WM (Mutter/Cinnamon) to
           offset maximised windows to y == panel_bottom, so a pure geometry
           check (y < panel_bottom) would miss them (36 < 36 == False).
        2. Any window whose top edge is spatially inside the panel row — catches
           non-maximised windows that happen to sit at y=0 (e.g. chromium in
           fullscreen-ish mode).
        """
        try:
            screen = Wnck.Screen.get_default()
            screen.force_update()
            panel_bottom = self._mon_y + self._height

            for win in screen.get_windows():
                if win.is_minimized():
                    continue
                wtype = win.get_window_type()
                if wtype in (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK):
                    continue
                x, y, w, h = win.get_client_window_geometry()
                # Must be on this monitor horizontally
                if x + w <= self._mon_x or x >= self._mon_x + self._mon_w:
                    continue

                state = win.get_state()
                # Maximised-vertically or fullscreen → hide even if WM placed
                # the window exactly at panel_bottom (no spatial overlap).
                if state & (Wnck.WindowState.MAXIMIZED_VERTICALLY |
                            Wnck.WindowState.FULLSCREEN):
                    return True
                # Non-maximised window whose top edge intrudes into the panel row.
                if y < panel_bottom:
                    return True
        except Exception as e:
            log.debug(f"[AutoHide] overlap check: {e}")
        return False

    # ── Internal show / hide ───────────────────────────────────────────────────

    def _show(self):
        self._cancel_hide()
        if self._hidden:
            self._hidden = False
            self._on_show()
            log.debug("[AutoHide] revealed")

    def _hide(self):
        self._cancel_hide()
        if not self._hidden:
            self._hidden = True
            self._on_hide()
            log.debug("[AutoHide] concealed")

    def _schedule_hide(self):
        if self._hide_id:
            return
        self._hide_id = GLib.timeout_add(self.HIDE_DELAY_MS, self._do_hide)

    def _cancel_hide(self):
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = None

    def _do_hide(self):
        self._hide_id = None
        self._hide()
        return False
