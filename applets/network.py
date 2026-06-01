"""applets/network.py — network status + popup via NetworkManager D-Bus."""

import logging
import subprocess

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango

import dbus

from .base  import Applet
from .popup import PanelPopup

log = logging.getLogger('pillpanel.network')

NM_STATE_CONNECTED_GLOBAL = 70
NM_STATE_CONNECTING       = 40
NM_DEVICE_TYPE_ETHERNET   = 1
NM_DEVICE_TYPE_WIFI       = 2
NM_DEVICE_STATE_ACTIVATED = 100
AP_FLAGS_PRIVACY          = 0x1   # any security (WEP/WPA)


class NetworkApplet(Applet):
    """Icon button that opens a NetworkPopup on click."""

    def build(self):
        self._btn = Gtk.Button()
        self._btn.set_relief(Gtk.ReliefStyle.NONE)
        self._btn.set_focus_on_click(False)
        self._btn.get_style_context().add_class('panel-btn')

        self._icon = Gtk.Image()
        self._btn.add(self._icon)

        self._sysbus   = self.panel.system_bus
        self._nm_props = None
        self._setup_nm()
        self._refresh()

        self._popup = NetworkPopup(self._sysbus)
        self._btn.connect('clicked', lambda b: self._popup.toggle(b))
        return self._btn

    def _setup_nm(self):
        try:
            obj = self._sysbus.get_object(
                'org.freedesktop.NetworkManager',
                '/org/freedesktop/NetworkManager',
            )
            self._nm_props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            self._sysbus.add_signal_receiver(
                lambda state: GLib.idle_add(self._refresh),
                signal_name='StateChanged',
                dbus_interface='org.freedesktop.NetworkManager',
                bus_name='org.freedesktop.NetworkManager',
            )
            log.info("[Network] Connected to NetworkManager")
        except Exception as e:
            log.error(f"[Network] Setup failed: {e}")

    def _refresh(self):
        if not self._nm_props:
            self._icon.set_from_icon_name('network-offline-symbolic', Gtk.IconSize.SMALL_TOOLBAR)
            return True
        try:
            state     = int(self._nm_props.Get('org.freedesktop.NetworkManager', 'State'))
            conn_type = str(self._nm_props.Get('org.freedesktop.NetworkManager', 'PrimaryConnectionType'))

            if state >= NM_STATE_CONNECTED_GLOBAL:
                if '802-11-wireless' in conn_type:
                    s    = self._wifi_strength()
                    icon = _wifi_icon(s)
                    self._btn.set_tooltip_text(f'WiFi  {s}%')
                elif 'ethernet' in conn_type or '802-3-ethernet' in conn_type:
                    icon = 'network-wired-symbolic'
                    self._btn.set_tooltip_text('Ethernet')
                else:
                    icon = 'network-transmit-receive-symbolic'
                    self._btn.set_tooltip_text('Connected')
            elif state >= NM_STATE_CONNECTING:
                icon = 'network-wireless-acquiring-symbolic'
                self._btn.set_tooltip_text('Connecting…')
            else:
                icon = 'network-offline-symbolic'
                self._btn.set_tooltip_text('No network')

            self._icon.set_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR)
        except Exception as e:
            log.error(f"[Network] Refresh: {e}")
        return True

    def _wifi_strength(self):
        try:
            for dev_path in self._nm_props.Get('org.freedesktop.NetworkManager', 'Devices'):
                dev  = self._sysbus.get_object('org.freedesktop.NetworkManager', dev_path)
                devp = dbus.Interface(dev, 'org.freedesktop.DBus.Properties')
                if int(devp.Get('org.freedesktop.NetworkManager.Device', 'DeviceType')) != NM_DEVICE_TYPE_WIFI:
                    continue
                ap_path = str(devp.Get('org.freedesktop.NetworkManager.Device.Wireless', 'ActiveAccessPoint'))
                if not ap_path or ap_path == '/':
                    continue
                ap  = self._sysbus.get_object('org.freedesktop.NetworkManager', ap_path)
                app = dbus.Interface(ap, 'org.freedesktop.DBus.Properties')
                return int(app.Get('org.freedesktop.NetworkManager.AccessPoint', 'Strength'))
        except Exception:
            pass
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkPopup
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkPopup:
    """
    Dropdown showing:
      • Wired connection status
      • WiFi on/off toggle
      • Sorted list of visible access points (strength, lock icon)
      • Network Settings / Network Connections buttons
    """

    WIDTH = 300

    def __init__(self, sysbus):
        self._sysbus          = sysbus
        self._ap_box          = None
        self._wired_lbl       = None
        self._wifi_switch     = None
        self._updating_switch = False   # prevent feedback loop on programmatic toggle

        self._popup = PanelPopup(self._build_content())

    def toggle(self, btn):
        if not self._popup._visible:
            self._refresh()
        self._popup.toggle(btn)

    # ── Static skeleton ────────────────────────────────────────────────────────

    def _build_content(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(self.WIDTH, -1)

        # ── Wired row ──────────────────────────────────────────────────────────
        wrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        wrow.set_margin_start(14)
        wrow.set_margin_end(14)
        wrow.set_margin_top(10)
        wrow.set_margin_bottom(6)

        wrow.pack_start(
            Gtk.Image.new_from_icon_name('network-wired-symbolic', Gtk.IconSize.MENU),
            False, False, 0,
        )
        self._wired_lbl = Gtk.Label()
        self._wired_lbl.set_halign(Gtk.Align.START)
        self._wired_lbl.get_style_context().add_class('panel-label')
        wrow.pack_start(self._wired_lbl, True, True, 0)
        root.pack_start(wrow, False, False, 0)

        root.pack_start(_sep(), False, False, 0)

        # ── Wireless toggle row ────────────────────────────────────────────────
        wifi_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        wifi_hdr.set_margin_start(14)
        wifi_hdr.set_margin_end(14)
        wifi_hdr.set_margin_top(6)
        wifi_hdr.set_margin_bottom(6)

        wifi_hdr.pack_start(
            Gtk.Image.new_from_icon_name('network-wireless-symbolic', Gtk.IconSize.MENU),
            False, False, 0,
        )
        lbl = Gtk.Label(label='Wireless')
        lbl.set_halign(Gtk.Align.START)
        lbl.get_style_context().add_class('panel-label')
        wifi_hdr.pack_start(lbl, True, True, 0)

        self._wifi_switch = Gtk.Switch()
        self._wifi_switch.connect('notify::active', self._on_wifi_toggle)
        wifi_hdr.pack_end(self._wifi_switch, False, False, 0)
        root.pack_start(wifi_hdr, False, False, 0)

        root.pack_start(_sep(), False, False, 0)

        # ── AP list ────────────────────────────────────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(280)
        scroll.set_propagate_natural_height(True)

        self._ap_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.add(self._ap_box)
        root.pack_start(scroll, True, True, 0)

        root.pack_start(_sep(), False, False, 0)

        # ── Bottom buttons ─────────────────────────────────────────────────────
        for label, cmd in [
            ('Network Settings',    ['nm-connection-editor']),
            ('Network Connections', ['nm-connection-editor', '--show']),
        ]:
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class('cal-settings-btn')
            b.set_halign(Gtk.Align.START)
            b.set_margin_start(8)
            b.connect('clicked', lambda _, c=cmd: self._hide_and_launch(c))
            root.pack_start(b, False, False, 0)

        root.pack_start(Gtk.Box(), False, False, 6)
        return root

    # ── Refresh ────────────────────────────────────────────────────────────────

    def _refresh(self):
        try:
            sysbus = self._sysbus
            obj    = sysbus.get_object(
                'org.freedesktop.NetworkManager',
                '/org/freedesktop/NetworkManager',
            )
            nm_props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            devices  = nm_props.Get('org.freedesktop.NetworkManager', 'Devices')

            # WiFi toggle state
            wifi_on = bool(nm_props.Get('org.freedesktop.NetworkManager', 'WirelessEnabled'))
            self._updating_switch = True
            self._wifi_switch.set_active(wifi_on)
            self._updating_switch = False

            # Wired status
            wired_text = 'cable unplugged'
            for dev_path in devices:
                dev  = sysbus.get_object('org.freedesktop.NetworkManager', dev_path)
                devp = dbus.Interface(dev, 'org.freedesktop.DBus.Properties')
                if int(devp.Get('org.freedesktop.NetworkManager.Device', 'DeviceType')) != NM_DEVICE_TYPE_ETHERNET:
                    continue
                if int(devp.Get('org.freedesktop.NetworkManager.Device', 'State')) == NM_DEVICE_STATE_ACTIVATED:
                    wired_text = 'connected'
                    break
            self._wired_lbl.set_text(wired_text)

            # Access points
            aps = []
            for dev_path in devices:
                dev  = sysbus.get_object('org.freedesktop.NetworkManager', dev_path)
                devp = dbus.Interface(dev, 'org.freedesktop.DBus.Properties')
                if int(devp.Get('org.freedesktop.NetworkManager.Device', 'DeviceType')) != NM_DEVICE_TYPE_WIFI:
                    continue

                wifi_if    = dbus.Interface(dev, 'org.freedesktop.NetworkManager.Device.Wireless')
                active_ap  = str(devp.Get('org.freedesktop.NetworkManager.Device.Wireless', 'ActiveAccessPoint'))

                seen = set()
                for ap_path in wifi_if.GetAccessPoints():
                    ap_obj = sysbus.get_object('org.freedesktop.NetworkManager', ap_path)
                    app    = dbus.Interface(ap_obj, 'org.freedesktop.DBus.Properties')
                    ssid   = bytes(app.Get('org.freedesktop.NetworkManager.AccessPoint', 'Ssid')).decode('utf-8', errors='replace').strip()
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    strength  = int(app.Get('org.freedesktop.NetworkManager.AccessPoint', 'Strength'))
                    flags     = int(app.Get('org.freedesktop.NetworkManager.AccessPoint', 'Flags'))
                    wpa       = int(app.Get('org.freedesktop.NetworkManager.AccessPoint', 'WpaFlags'))
                    rsn       = int(app.Get('org.freedesktop.NetworkManager.AccessPoint', 'RsnFlags'))
                    aps.append({
                        'ssid':     ssid,
                        'strength': strength,
                        'secured':  bool(flags & AP_FLAGS_PRIVACY) or bool(wpa) or bool(rsn),
                        'active':   str(ap_path) == active_ap,
                    })
                break   # first WiFi device is enough

            aps.sort(key=lambda x: (x['active'], x['strength']), reverse=True)
            self._rebuild_ap_list(aps)

        except Exception as e:
            log.error(f"[Network] Popup refresh: {e}")

    def _rebuild_ap_list(self, aps):
        for ch in self._ap_box.get_children():
            self._ap_box.remove(ch)

        if not aps:
            lbl = Gtk.Label(label='No networks found')
            lbl.get_style_context().add_class('cal-dim')
            lbl.set_margin_top(12)
            lbl.set_margin_bottom(12)
            self._ap_box.pack_start(lbl, False, False, 0)
        else:
            for ap in aps:
                self._ap_box.pack_start(self._build_ap_row(ap), False, False, 0)

        self._ap_box.show_all()

    def _build_ap_row(self, ap):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class('net-row-btn')

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(5)
        row.set_margin_bottom(5)

        # Signal strength icon
        row.pack_start(
            Gtk.Image.new_from_icon_name(_wifi_icon(ap['strength']), Gtk.IconSize.SMALL_TOOLBAR),
            False, False, 0,
        )

        # SSID
        ssid_lbl = Gtk.Label()
        ssid_lbl.set_halign(Gtk.Align.START)
        ssid_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        ssid_lbl.get_style_context().add_class('panel-label')
        if ap['active']:
            ssid_lbl.set_markup(f'<b>{GLib.markup_escape_text(ap["ssid"])}</b>')
        else:
            ssid_lbl.set_text(ap['ssid'])
        row.pack_start(ssid_lbl, True, True, 0)

        # Signal %
        pct = Gtk.Label(label=f'{ap["strength"]}%')
        pct.get_style_context().add_class('cal-dim')
        row.pack_end(pct, False, False, 0)

        # Lock icon
        if ap['secured']:
            lock = Gtk.Image.new_from_icon_name('channel-secure-symbolic', Gtk.IconSize.MENU)
            lock.get_style_context().add_class('cal-dim')
            row.pack_end(lock, False, False, 2)

        # Active checkmark
        if ap['active']:
            check = Gtk.Image.new_from_icon_name('object-select-symbolic', Gtk.IconSize.MENU)
            row.pack_end(check, False, False, 0)

        btn.add(row)
        return btn

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _on_wifi_toggle(self, switch, _param):
        if self._updating_switch:
            return
        try:
            obj = self._sysbus.get_object(
                'org.freedesktop.NetworkManager',
                '/org/freedesktop/NetworkManager',
            )
            dbus.Interface(obj, 'org.freedesktop.DBus.Properties').Set(
                'org.freedesktop.NetworkManager',
                'WirelessEnabled',
                dbus.Boolean(switch.get_active()),
            )
        except Exception as e:
            log.error(f"[Network] WiFi toggle: {e}")

    def _hide_and_launch(self, cmd):
        self._popup.hide()
        _launch(cmd)


# ── Module-level helpers ───────────────────────────────────────────────────────

def _wifi_icon(strength):
    if strength >= 80: return 'network-wireless-signal-excellent-symbolic'
    if strength >= 60: return 'network-wireless-signal-good-symbolic'
    if strength >= 40: return 'network-wireless-signal-ok-symbolic'
    if strength > 0:   return 'network-wireless-signal-weak-symbolic'
    return 'network-wireless-signal-none-symbolic'

def _sep():
    s = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    s.set_margin_top(2)
    s.set_margin_bottom(2)
    return s

def _launch(cmd):
    try:
        subprocess.Popen(cmd)
    except FileNotFoundError:
        pass
