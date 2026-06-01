"""applets/volume.py — volume control using pactl (PipeWire-pulse / PulseAudio)."""

import re
import logging
import subprocess

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from .base  import Applet
from .popup import PanelPopup

log = logging.getLogger('pillpanel.volume')


class VolumeApplet(Applet):
    """
    Shows a volume icon that reflects the current default sink volume.
    • Scroll wheel: ±5% volume
    • Click: opens a popup with a slider and mute toggle
    Polls pactl every second for state changes (covers external changes too).
    """

    def build(self):
        self._btn = Gtk.Button()
        self._btn.set_relief(Gtk.ReliefStyle.NONE)
        self._btn.set_focus_on_click(False)
        self._btn.get_style_context().add_class('panel-btn')
        self._btn.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)

        self._icon = Gtk.Image()
        self._btn.add(self._icon)

        self._volume = 50
        self._muted  = False
        self._refresh()
        self._timer = GLib.timeout_add(1000, self._refresh)

        self._popup = self._build_popup()
        self._btn.connect('clicked',      self._on_click)
        self._btn.connect('scroll-event', self._on_scroll)
        return self._btn

    # ── Popup ──────────────────────────────────────────────────────────────────

    def _build_popup(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(280, -1)

        # ── Slider row: [speaker btn] [slider] [pct] ──────────────────────────
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_margin_start(10)
        row.set_margin_end(14)
        row.set_margin_top(12)
        row.set_margin_bottom(8)

        # Speaker button — click to mute/unmute
        self._mute_btn = Gtk.Button()
        self._mute_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._mute_btn.set_focus_on_click(False)
        self._mute_btn.get_style_context().add_class('panel-btn')
        self._popup_icon = Gtk.Image()
        self._mute_btn.add(self._popup_icon)
        self._mute_btn.connect('clicked', self._on_mute_toggle)
        row.pack_start(self._mute_btn, False, False, 0)

        # Slider
        self._slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._slider.set_draw_value(True)
        self._slider.set_value_pos(Gtk.PositionType.TOP)
        self._slider.connect('format-value', lambda _, v: f'{int(v)}%')
        self._slider.set_hexpand(True)
        self._slider.get_style_context().add_class('vol-slider')
        self._slider.connect('value-changed', self._on_slider_moved)
        row.pack_start(self._slider, True, True, 0)


        root.pack_start(row, False, False, 0)

        # Separator + Sound Settings button
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(2)
        sep.set_margin_bottom(2)
        root.pack_start(sep, False, False, 0)

        settings = Gtk.Button(label='Sound Settings')
        settings.set_relief(Gtk.ReliefStyle.NONE)
        settings.get_style_context().add_class('cal-settings-btn')
        settings.set_halign(Gtk.Align.START)
        settings.set_margin_start(8)
        settings.connect('clicked', lambda _: self._open_settings())
        root.pack_start(settings, False, False, 0)

        root.pack_start(Gtk.Box(), False, False, 4)

        return PanelPopup(root)

    # ── pactl helpers ──────────────────────────────────────────────────────────

    def _get_volume(self):
        try:
            r = subprocess.run(
                ['pactl', 'get-sink-volume', '@DEFAULT_SINK@'],
                capture_output=True, text=True, timeout=2,
            )
            m = re.search(r'(\d+)%', r.stdout)
            return int(m.group(1)) if m else 50
        except Exception as e:
            log.error(f"[Volume] get-volume: {e}")
            return 50

    def _get_muted(self):
        try:
            r = subprocess.run(
                ['pactl', 'get-sink-mute', '@DEFAULT_SINK@'],
                capture_output=True, text=True, timeout=2,
            )
            return 'yes' in r.stdout.lower()
        except Exception:
            return False

    def _set_volume(self, pct):
        subprocess.run(
            ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{int(pct)}%'],
            capture_output=True,
        )

    def _set_mute(self, muted):
        subprocess.run(
            ['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if muted else '0'],
            capture_output=True,
        )

    # ── State refresh ──────────────────────────────────────────────────────────

    def _refresh(self):
        self._volume = self._get_volume()
        self._muted  = self._get_muted()
        self._update_icon()
        return True

    def _update_icon(self):
        name = self._icon_name()
        self._icon.set_from_icon_name(name, Gtk.IconSize.SMALL_TOOLBAR)
        tip = f'{self._volume}%' + (' (muted)' if self._muted else '')
        self._btn.set_tooltip_text(tip)

    def _update_popup_widgets(self):
        self._slider.handler_block_by_func(self._on_slider_moved)
        self._slider.set_value(self._volume)
        self._slider.handler_unblock_by_func(self._on_slider_moved)
        # Mirror the panel icon inside the popup speaker button
        self._popup_icon.set_from_icon_name(
            self._icon_name(), Gtk.IconSize.SMALL_TOOLBAR
        )

    def _icon_name(self):
        if self._muted or self._volume == 0:
            return 'audio-volume-muted-symbolic'
        if self._volume < 33:
            return 'audio-volume-low-symbolic'
        if self._volume < 66:
            return 'audio-volume-medium-symbolic'
        return 'audio-volume-high-symbolic'

    def _open_settings(self):
        self._popup.hide()
        for cmd in (['gnome-control-center', 'sound'],
                    ['cinnamon-settings', 'sound'],
                    ['pavucontrol']):
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue

    # ── Handlers ───────────────────────────────────────────────────────────────

    def _on_click(self, btn):
        self._refresh()
        self._update_popup_widgets()
        self._popup.toggle(btn)

    def _on_slider_moved(self, scale):
        vol = int(scale.get_value())
        self._volume = vol
        self._set_volume(vol)
        self._update_icon()

    def _on_mute_toggle(self, _btn):
        self._muted = not self._muted
        self._set_mute(self._muted)
        self._update_icon()
        self._update_popup_widgets()

    def _on_scroll(self, _widget, event):
        if event.direction == Gdk.ScrollDirection.UP:
            delta = +5
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = -5
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            delta = -5 if event.delta_y > 0 else +5
        else:
            return False
        new_vol = max(0, min(100, self._volume + delta))
        self._set_volume(new_vol)
        self._volume = new_vol
        self._update_icon()
        return True

    def destroy(self):
        GLib.source_remove(self._timer)
