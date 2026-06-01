"""applets/battery.py — battery status + popup via UPower D-Bus."""

import logging
import subprocess

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango

import dbus

from .base  import Applet
from .popup import PanelPopup

log = logging.getLogger('pillpanel.battery')

# UPower state codes
UP_STATE_CHARGING    = 1
UP_STATE_DISCHARGING = 2
UP_STATE_EMPTY       = 3
UP_STATE_FULL        = 4
UP_STATE_PENDING_CHG = 5
UP_TYPE_LINE_POWER   = 1
UP_TYPE_BATTERY      = 2

# Power profiles daemon (net.hadess.PowerProfiles / Ubuntu ppd)
PP_BUS   = 'net.hadess.PowerProfiles'
PP_PATH  = '/net/hadess/PowerProfiles'
PP_IFACE = 'net.hadess.PowerProfiles'

PROFILES = [
    ('power-saver', 'Power Saver'),
    ('balanced',    'Balanced'),
    ('performance', 'Performance'),
]

_STATE_LABEL = {
    UP_STATE_CHARGING:    'Charging',
    UP_STATE_DISCHARGING: 'Discharging',
    UP_STATE_EMPTY:       'Empty',
    UP_STATE_FULL:        'Fully charged',
    UP_STATE_PENDING_CHG: 'Charging',
}


class BatteryApplet(Applet):
    """
    Shows battery icon + percentage.
    Subscribes to UPower PropertiesChanged for immediate updates.
    Hidden when no battery is present (desktops).
    """

    def build(self):
        self._btn = Gtk.Button()
        self._btn.set_relief(Gtk.ReliefStyle.NONE)
        self._btn.set_focus_on_click(False)
        self._btn.get_style_context().add_class('panel-btn')

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self._icon  = Gtk.Image()
        self._label = Gtk.Label()
        self._label.get_style_context().add_class('panel-label')
        row.pack_start(self._icon,  False, False, 0)
        row.pack_start(self._label, False, False, 0)
        self._btn.add(row)

        self._dd_props = None
        self._setup_upower()
        self._refresh()

        self._popup = BatteryPopup(self.panel.system_bus, self._dd_props)
        self._btn.connect('clicked', lambda b: self._popup.toggle(b))
        return self._btn

    def _setup_upower(self):
        try:
            sysbus  = self.panel.system_bus
            up      = sysbus.get_object('org.freedesktop.UPower', '/org/freedesktop/UPower')
            dd_path = str(dbus.Interface(up, 'org.freedesktop.UPower').GetDisplayDevice())
            dd      = sysbus.get_object('org.freedesktop.UPower', dd_path)
            self._dd_props = dbus.Interface(dd, 'org.freedesktop.DBus.Properties')
            sysbus.add_signal_receiver(
                self._on_props_changed,
                signal_name='PropertiesChanged',
                dbus_interface='org.freedesktop.DBus.Properties',
                bus_name='org.freedesktop.UPower',
                path=dd_path,
            )
            log.info("[Battery] Connected to UPower display device")
        except Exception as e:
            log.error(f"[Battery] Setup failed: {e}")

    def _refresh(self):
        if not self._dd_props:
            self._btn.hide()
            return
        try:
            dtype = int(self._dd_props.Get('org.freedesktop.UPower.Device', 'Type'))
            if dtype == UP_TYPE_LINE_POWER:
                self._btn.hide()
                return

            pct   = float(self._dd_props.Get('org.freedesktop.UPower.Device', 'Percentage'))
            state = int(self._dd_props.Get('org.freedesktop.UPower.Device', 'State'))

            if state in (UP_STATE_CHARGING, UP_STATE_PENDING_CHG):
                icon_name = 'battery-charging-symbolic'
            elif state == UP_STATE_FULL:
                icon_name = 'battery-full-charged-symbolic'
            elif pct >= 80:
                icon_name = 'battery-full-symbolic'
            elif pct >= 50:
                icon_name = 'battery-good-symbolic'
            elif pct >= 20:
                icon_name = 'battery-caution-symbolic'
            else:
                icon_name = 'battery-low-symbolic'

            self._icon.set_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
            self._label.set_markup(
                f'<span foreground="white" font="10">{pct:.0f}%</span>'
            )
            self._btn.show()

            tip = f'{pct:.0f}%'
            if state in (UP_STATE_CHARGING, UP_STATE_PENDING_CHG):
                tip += ' — charging'
                try:
                    ttf = int(self._dd_props.Get('org.freedesktop.UPower.Device', 'TimeToFull'))
                    if ttf > 0:
                        h, m = divmod(ttf // 60, 60)
                        tip += f' ({h}h {m:02d}m to full)'
                except Exception:
                    pass
            elif state == UP_STATE_DISCHARGING:
                try:
                    tte = int(self._dd_props.Get('org.freedesktop.UPower.Device', 'TimeToEmpty'))
                    if tte > 0:
                        h, m = divmod(tte // 60, 60)
                        tip += f' — {h}h {m:02d}m remaining'
                except Exception:
                    pass
            self._btn.set_tooltip_text(tip)
        except Exception as e:
            log.error(f"[Battery] Refresh failed: {e}")

    def _on_props_changed(self, iface, changed, invalidated):
        GLib.idle_add(self._refresh)


# ═══════════════════════════════════════════════════════════════════════════════
# BatteryPopup
# ═══════════════════════════════════════════════════════════════════════════════

class BatteryPopup:
    """
    Dropdown showing:
      • Battery name, percentage, and status
      • Charge level bar
      • Power profile selector (if power-profiles-daemon is available)
      • Power Settings button
    """

    WIDTH = 260

    def __init__(self, sysbus, dd_props):
        self._sysbus   = sysbus
        self._dd_props = dd_props
        self._pp_props = None
        self._profile_btns = {}

        self._setup_power_profiles()
        self._popup = PanelPopup(self._build_content())

    def toggle(self, btn):
        if not self._popup._visible:
            self._refresh()
        self._popup.toggle(btn)

    # ── Power-profiles-daemon ──────────────────────────────────────────────────

    def _setup_power_profiles(self):
        try:
            obj = self._sysbus.get_object(PP_BUS, PP_PATH)
            self._pp_props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            log.debug("[Battery] Power profiles daemon available")
        except Exception:
            log.debug("[Battery] Power profiles daemon not available")

    # ── Build static skeleton ──────────────────────────────────────────────────

    def _build_content(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(self.WIDTH, -1)

        # ── Header: icon + name + status ───────────────────────────────────────
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hdr.set_margin_start(14)
        hdr.set_margin_end(14)
        hdr.set_margin_top(12)
        hdr.set_margin_bottom(6)

        self._hdr_icon = Gtk.Image.new_from_icon_name(
            'battery-full-symbolic', Gtk.IconSize.LARGE_TOOLBAR
        )
        hdr.pack_start(self._hdr_icon, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._name_lbl = Gtk.Label()
        self._name_lbl.set_halign(Gtk.Align.START)
        self._name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._name_lbl.get_style_context().add_class('panel-label')
        info.pack_start(self._name_lbl, False, False, 0)

        self._status_lbl = Gtk.Label()
        self._status_lbl.set_halign(Gtk.Align.START)
        self._status_lbl.get_style_context().add_class('cal-dim')
        info.pack_start(self._status_lbl, False, False, 0)

        hdr.pack_start(info, True, True, 0)
        root.pack_start(hdr, False, False, 0)

        # ── Charge level bar ───────────────────────────────────────────────────
        self._level = Gtk.LevelBar.new_for_interval(0, 100)
        self._level.set_mode(Gtk.LevelBarMode.CONTINUOUS)
        self._level.set_hexpand(True)
        self._level.get_style_context().add_class('bat-level')
        self._level.set_margin_start(14)
        self._level.set_margin_end(14)
        self._level.set_margin_bottom(10)
        root.pack_start(self._level, False, False, 0)

        # ── Power profiles (hidden if ppd not available) ───────────────────────
        if self._pp_props:
            root.pack_start(_sep(), False, False, 0)
            for key, label in PROFILES:
                btn = Gtk.Button()
                btn.set_relief(Gtk.ReliefStyle.NONE)
                btn.get_style_context().add_class('cal-settings-btn')
                btn.set_halign(Gtk.Align.START)
                btn.set_margin_start(8)
                btn.connect('clicked', lambda _, k=key: self._set_profile(k))
                self._profile_btns[key] = btn
                root.pack_start(btn, False, False, 0)

        # ── Power Settings ─────────────────────────────────────────────────────
        root.pack_start(_sep(), False, False, 0)
        settings = Gtk.Button(label='Power Settings')
        settings.set_relief(Gtk.ReliefStyle.NONE)
        settings.get_style_context().add_class('cal-settings-btn')
        settings.set_halign(Gtk.Align.START)
        settings.set_margin_start(8)
        settings.connect('clicked', lambda _: self._hide_and_open_settings())
        root.pack_start(settings, False, False, 0)

        root.pack_start(Gtk.Box(), False, False, 4)
        return root

    # ── Refresh ────────────────────────────────────────────────────────────────

    def _refresh(self):
        if not self._dd_props:
            return
        try:
            pct    = float(self._dd_props.Get('org.freedesktop.UPower.Device', 'Percentage'))
            state  = int(self._dd_props.Get('org.freedesktop.UPower.Device', 'State'))
            model  = str(self._dd_props.Get('org.freedesktop.UPower.Device', 'Model')).strip()

            # Header
            name_text = f'{model}  {pct:.0f}%' if model else f'{pct:.0f}%'
            self._name_lbl.set_markup(f'<b>{GLib.markup_escape_text(name_text)}</b>')
            self._status_lbl.set_text(_STATE_LABEL.get(state, 'Unknown'))

            # Icon
            if state in (UP_STATE_CHARGING, UP_STATE_PENDING_CHG):
                icon = 'battery-charging-symbolic'
            elif state == UP_STATE_FULL:
                icon = 'battery-full-charged-symbolic'
            elif pct >= 80:
                icon = 'battery-full-symbolic'
            elif pct >= 50:
                icon = 'battery-good-symbolic'
            elif pct >= 20:
                icon = 'battery-caution-symbolic'
            else:
                icon = 'battery-low-symbolic'
            self._hdr_icon.set_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)

            # Level bar
            self._level.set_value(pct)

            # Power profiles
            if self._pp_props and self._profile_btns:
                try:
                    current = str(self._pp_props.Get(PP_IFACE, 'ActiveProfile'))
                    for key, label in PROFILES:
                        btn = self._profile_btns.get(key)
                        if btn:
                            btn.set_label(f'• {label}' if key == current else f'  {label}')
                except Exception:
                    pass

        except Exception as e:
            log.error(f"[Battery] Popup refresh: {e}")

    def _hide_and_open_settings(self):
        self._popup.hide()
        _open_power_settings()

    def _set_profile(self, profile):
        try:
            obj = self._sysbus.get_object(PP_BUS, PP_PATH)
            dbus.Interface(obj, 'org.freedesktop.DBus.Properties').Set(
                PP_IFACE, 'ActiveProfile', dbus.String(profile)
            )
            GLib.timeout_add(200, self._refresh)
        except Exception as e:
            log.error(f"[Battery] Set profile '{profile}': {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sep():
    s = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    s.set_margin_top(2)
    s.set_margin_bottom(2)
    return s

def _open_power_settings():
    for cmd in (['cinnamon-settings', 'power'],
                ['gnome-control-center', 'power']):
        try:
            subprocess.Popen(cmd)
            return
        except FileNotFoundError:
            continue
