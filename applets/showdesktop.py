"""applets/showdesktop.py — toggle showing the desktop via Wnck."""

import logging

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Wnck', '3.0')
from gi.repository import Gtk, Wnck

from .base import Applet

log = logging.getLogger('pillpanel.showdesktop')


class ShowDesktopApplet(Applet):
    """
    Vertical separator + button that minimises all windows to reveal the
    desktop. Clicking again restores them.
    """

    def build(self):
        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # Vertical separator — no top/bottom margin so it spans the full pill
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_start(2)
        sep.set_margin_end(2)
        container.pack_start(sep, False, False, 0)

        # Button
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class('panel-btn')
        btn.set_tooltip_text('Show Desktop')

        self._icon = Gtk.Image.new_from_icon_name(
            'user-desktop-symbolic', Gtk.IconSize.SMALL_TOOLBAR
        )
        btn.add(self._icon)
        btn.connect('clicked', self._on_click)

        container.pack_start(btn, False, False, 0)
        return container

    def after_icon_size(self):
        """Called by PillPanel after the global icon-size walk, so the custom
        show-desktop size is applied last and isn't overwritten."""
        px = self.panel.config.get('show_desktop_icon_size', 16)
        self._icon.set_pixel_size(px)

    def _on_click(self, _btn):
        screen = Wnck.Screen.get_default()
        screen.force_update()
        currently_showing = screen.get_showing_desktop()
        screen.toggle_showing_desktop(not currently_showing)
        log.info(f"[ShowDesktop] was={currently_showing} → now={not currently_showing}")
