"""
applets/tray.py — system tray applet for PillPanel.

Combines two tray icon systems:
  1. SNI (StatusNotifierItem / KDE spec) — used by ClickUp, blueman, Discord, etc.
  2. XApp StatusIcon (Linux Mint native) — used by mintUpdate, nvidia-prime, etc.

The SNI side implements both the Watcher (org.kde.StatusNotifierWatcher) and
the Host (org.kde.StatusNotifierHost-<pid>) in one process.
The XApp side uses XApp.StatusIconMonitor which handles discovery automatically.
"""

import os
import sys
import logging

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('XApp', '1.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, XApp

import dbus
import dbus.service

from .base import Applet

log = logging.getLogger('pillpanel.tray')

# ── D-Bus constants ────────────────────────────────────────────────────────────

WATCHER_BUS_NAME  = "org.kde.StatusNotifierWatcher"
WATCHER_OBJ_PATH  = "/StatusNotifierWatcher"
WATCHER_IFACE     = "org.kde.StatusNotifierWatcher"
SNI_IFACE         = "org.kde.StatusNotifierItem"
DBUSMENU_IFACE    = "com.canonical.dbusmenu"
PROPS_IFACE       = "org.freedesktop.DBus.Properties"

HOST_BUS_NAME = f"org.kde.StatusNotifierHost-{os.getpid()}"
ICON_SIZE     = 16

# ── Watcher introspection XML ──────────────────────────────────────────────────

_WATCHER_XML = """
<!DOCTYPE node PUBLIC
  "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
  "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem">
      <arg type="s" name="service" direction="in"/>
    </method>
    <method name="RegisterStatusNotifierHost">
      <arg type="s" name="service" direction="in"/>
    </method>
    <signal name="StatusNotifierItemRegistered">
      <arg type="s" name="service"/>
    </signal>
    <signal name="StatusNotifierItemUnregistered">
      <arg type="s" name="service"/>
    </signal>
    <signal name="StatusNotifierHostRegistered"/>
    <property type="as" name="RegisteredStatusNotifierItems" access="read"/>
    <property type="b"  name="IsStatusNotifierHostRegistered" access="read"/>
    <property type="i"  name="ProtocolVersion" access="read"/>
  </interface>
</node>
"""


# ═══════════════════════════════════════════════════════════════════════════════
# StatusNotifierWatcher
# ═══════════════════════════════════════════════════════════════════════════════

class StatusNotifierWatcher(dbus.service.Object):
    """
    Implements org.kde.StatusNotifierWatcher — the singleton broker that:
      - receives registrations from SNI items (apps wanting a tray icon)
      - receives registrations from SNI hosts (panels wanting to show icons)
      - notifies hosts when items come and go

    Also claims org.x.StatusNotifierWatcher (Linux Mint / xapp variant) so that
    apps targeting that name also register with us.
    """

    def __init__(self, bus, on_item_added=None, on_item_removed=None):
        self._bus = bus
        self._items = []
        self._hosts = []
        self._on_item_added   = on_item_added
        self._on_item_removed = on_item_removed

        try:
            bn = dbus.service.BusName(
                WATCHER_BUS_NAME, bus,
                do_not_queue=True, replace_existing=False, allow_replacement=False,
            )
        except dbus.DBusException as e:
            log.error(
                f"\n*** Cannot acquire {WATCHER_BUS_NAME} ***\n"
                f"    {e}\n"
                f"    Kill the existing watcher:  pkill xapp-sn-watcher\n"
                f"    Then restart PillPanel."
            )
            sys.exit(1)

        super().__init__(bn, WATCHER_OBJ_PATH)
        log.info(f"[Watcher] Acquired {WATCHER_BUS_NAME}")

        # Also claim the Linux Mint xapp variant name
        try:
            self._x_bn = dbus.service.BusName(
                "org.x.StatusNotifierWatcher", bus,
                do_not_queue=True, replace_existing=False, allow_replacement=False,
            )
            log.info("[Watcher] Also acquired org.x.StatusNotifierWatcher")
        except dbus.DBusException as e:
            self._x_bn = None
            log.warning(f"[Watcher] Could not claim org.x.StatusNotifierWatcher: {e}")

        bus.add_signal_receiver(
            self._on_name_owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus",
            bus_name="org.freedesktop.DBus",
        )

    # ── D-Bus methods ──────────────────────────────────────────────────────────

    @dbus.service.method(WATCHER_IFACE, in_signature='s', sender_keyword='sender')
    def RegisterStatusNotifierItem(self, service, sender=None):
        """
        Normalise the service argument to "busname/objectpath" canonical form.
        Three formats arrive in the wild:
          "/some/path"            → sender + path
          "busname/some/path"     → already canonical
          "bare.bus.Name"         → bus name + /StatusNotifierItem
        """
        if service.startswith('/'):
            key = f"{sender}{service}"
        elif '/' in service:
            key = service
        else:
            key = f"{service}/StatusNotifierItem"

        if key in self._items:
            return

        self._items.append(key)
        log.info(f"[Watcher] SNI registered: {key}")
        self.StatusNotifierItemRegistered(key)
        if self._on_item_added:
            GLib.idle_add(self._on_item_added, key)

    @dbus.service.method(WATCHER_IFACE, in_signature='s', sender_keyword='sender')
    def RegisterStatusNotifierHost(self, service, sender=None):
        if service not in self._hosts:
            self._hosts.append(service)
            log.info(f"[Watcher] Host registered: {service}")
            self.StatusNotifierHostRegistered()

    # ── Signals ────────────────────────────────────────────────────────────────

    @dbus.service.signal(WATCHER_IFACE, signature='s')
    def StatusNotifierItemRegistered(self, service): pass

    @dbus.service.signal(WATCHER_IFACE, signature='s')
    def StatusNotifierItemUnregistered(self, service): pass

    @dbus.service.signal(WATCHER_IFACE)
    def StatusNotifierHostRegistered(self): pass

    # ── Properties ─────────────────────────────────────────────────────────────

    @dbus.service.method(PROPS_IFACE, in_signature='ss', out_signature='v')
    def Get(self, iface, prop):
        return self.GetAll(iface)[prop]

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, iface):
        return {
            'RegisteredStatusNotifierItems': dbus.Array(self._items, signature='s'),
            'IsStatusNotifierHostRegistered': dbus.Boolean(bool(self._hosts)),
            'ProtocolVersion': dbus.Int32(0),
        }

    @dbus.service.method(PROPS_IFACE, in_signature='ssv')
    def Set(self, iface, prop, value): pass

    @dbus.service.method(dbus.INTROSPECTABLE_IFACE, out_signature='s')
    def Introspect(self):
        return _WATCHER_XML

    # ── Dead-name cleanup ──────────────────────────────────────────────────────

    def _on_name_owner_changed(self, name, old_owner, new_owner):
        if new_owner != '':
            return
        dead = [k for k in self._items
                if k.startswith(old_owner + '/') or k.startswith(name + '/')]
        for key in dead:
            log.info(f"[Watcher] Owner gone ({name}), removing: {key}")
            self._items.remove(key)
            self.StatusNotifierItemUnregistered(key)
            if self._on_item_removed:
                GLib.idle_add(self._on_item_removed, key)


# ═══════════════════════════════════════════════════════════════════════════════
# TrayItem — one SNI application icon
# ═══════════════════════════════════════════════════════════════════════════════

class TrayItem:
    """
    Connects to one SNI item's D-Bus object, reads its properties (icon,
    title, menu path), and provides a GTK button widget.
    """

    def __init__(self, bus, item_key, debug=False):
        self.item_key = item_key
        self._bus     = bus
        self._debug   = debug
        self.button   = None

        slash = item_key.index('/')
        self.bus_name = item_key[:slash]
        self.obj_path = item_key[slash:]

        log.info(f"[SNI] Connecting  bus={self.bus_name!r}  path={self.obj_path!r}")

        try:
            proxy = bus.get_object(self.bus_name, self.obj_path)
            self._iface = dbus.Interface(proxy, SNI_IFACE)
            self._props = dbus.Interface(proxy, PROPS_IFACE)
        except dbus.DBusException as e:
            log.error(f"[SNI] {item_key}: proxy failed: {e}")
            self._iface = self._props = None

        self._data = {}
        self._load_properties()
        self._subscribe_signals()

    def _load_properties(self):
        if not self._props:
            return
        try:
            self._data = dict(self._props.GetAll(SNI_IFACE))
            if self._debug:
                log.debug(f"[SNI] {self.item_key} props: {sorted(self._data.keys())}")
        except dbus.DBusException as e:
            log.error(f"[SNI] {self.item_key}: GetAll failed: {e}")

    def _prop(self, name, default=None):
        return self._data.get(name, default)

    def _subscribe_signals(self):
        for sig in ("NewIcon", "NewAttentionIcon", "NewOverlayIcon"):
            try:
                self._bus.add_signal_receiver(
                    self._on_new_icon,
                    signal_name=sig, dbus_interface=SNI_IFACE,
                    bus_name=self.bus_name, path=self.obj_path,
                )
            except Exception:
                pass
        try:
            self._bus.add_signal_receiver(
                lambda s: log.info(f"[SNI] {self.item_key}: NewStatus → {s!r}"),
                signal_name="NewStatus", dbus_interface=SNI_IFACE,
                bus_name=self.bus_name, path=self.obj_path,
            )
        except Exception:
            pass

    # ── Widget ─────────────────────────────────────────────────────────────────

    def build_button(self):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class("tray-btn")
        self.button = btn
        self._apply_icon()
        btn.set_tooltip_text(str(self._prop('Title') or self._prop('Id') or self.item_key))
        btn.connect('clicked', self._on_left_click)
        btn.connect('button-press-event', self._on_button_press)
        btn.show_all()
        return btn

    def _apply_icon(self):
        if not self.button:
            return
        child = self.button.get_child()
        if child:
            self.button.remove(child)
        self.button.add(self._make_icon_widget())
        self.button.show_all()

    def _make_icon_widget(self):
        icon_name = str(self._prop('IconName') or '')
        if icon_name:
            log.info(f"[SNI] {self.item_key}: IconName={icon_name!r}")
            # Some apps (e.g. AppIndicator-based) ship icons outside the system
            # theme and advertise the directory via IconThemePath.
            theme_path = str(self._prop('IconThemePath') or '')
            if theme_path:
                theme = Gtk.IconTheme.get_default()
                if theme_path not in theme.get_search_path():
                    theme.prepend_search_path(theme_path)
                    log.info(f"[SNI] {self.item_key}: added icon theme path: {theme_path!r}")
            img = Gtk.Image()
            img.set_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
            img.set_pixel_size(ICON_SIZE)
            return img

        pixmaps = self._prop('IconPixmap')
        if pixmaps:
            pb = self._pixmaps_to_pixbuf(pixmaps)
            if pb:
                log.info(f"[SNI] {self.item_key}: using IconPixmap")
                return Gtk.Image.new_from_pixbuf(pb)

        log.warning(f"[SNI] {self.item_key}: no icon")
        return Gtk.Label(label=str(self._prop('Id') or '?')[:4])

    def _pixmaps_to_pixbuf(self, pixmaps):
        """Convert SNI ARGB32 big-endian pixmap data to GdkPixbuf."""
        best_w = best_h = 0
        best_data = None
        for pw, ph, pdata in pixmaps:
            pw, ph = int(pw), int(ph)
            if best_data is None or abs(pw - ICON_SIZE) < abs(best_w - ICON_SIZE):
                best_w, best_h, best_data = pw, ph, pdata
        if not best_data:
            return None
        raw  = bytes(best_data)
        n    = len(raw) // 4
        rgba = bytearray(n * 4)
        for i in range(n):
            o = i * 4
            a, r, g, b = raw[o], raw[o+1], raw[o+2], raw[o+3]
            rgba[o], rgba[o+1], rgba[o+2], rgba[o+3] = r, g, b, a
        try:
            pb = GdkPixbuf.Pixbuf.new_from_data(
                bytes(rgba), GdkPixbuf.Colorspace.RGB, True, 8,
                best_w, best_h, best_w * 4,
            )
            if best_w != ICON_SIZE or best_h != ICON_SIZE:
                pb = pb.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
            return pb
        except Exception as e:
            log.error(f"[SNI] {self.item_key}: pixmap→pixbuf: {e}")
            return None

    # ── Signal callbacks ───────────────────────────────────────────────────────

    def _on_new_icon(self, *_):
        log.info(f"[SNI] {self.item_key}: NewIcon → refreshing")
        self._load_properties()
        GLib.idle_add(self._apply_icon)

    # ── Click handlers ─────────────────────────────────────────────────────────

    def _on_left_click(self, btn):
        if not self._iface:
            return
        x, y = _btn_screen_pos(btn)
        log.info(f"[SNI] {self.item_key}: Activate({x},{y})")
        try:
            self._iface.Activate(dbus.Int32(x), dbus.Int32(y))
        except dbus.DBusException as e:
            log.warning(f"[SNI] {self.item_key}: Activate failed (menu-only?): {e}")

    def _on_button_press(self, btn, event):
        if event.button != 3:
            return False
        x, y = int(event.x_root), int(event.y_root)
        log.info(f"[SNI] {self.item_key}: right-click at ({x},{y})")
        menu_path = self._prop('Menu')
        if menu_path and str(menu_path) not in ('/', ''):
            menu = self._build_dbusmenu(str(menu_path))
            if menu:
                menu.popup_at_pointer(event)
                return True
        try:
            self._iface.ContextMenu(dbus.Int32(x), dbus.Int32(y))
        except dbus.DBusException as e:
            log.warning(f"[SNI] {self.item_key}: ContextMenu failed: {e}")
        return True

    # ── DBusMenu ───────────────────────────────────────────────────────────────

    def _build_dbusmenu(self, menu_path):
        try:
            proxy     = self._bus.get_object(self.bus_name, menu_path)
            menu_iface = dbus.Interface(proxy, DBUSMENU_IFACE)
            try:
                menu_iface.AboutToShow(dbus.Int32(0))
            except dbus.DBusException:
                pass
            _rev, layout = menu_iface.GetLayout(
                dbus.Int32(0), dbus.Int32(-1), dbus.Array([], signature='s'),
            )
            _, _, children = layout
            log.info(f"[SNI] {self.item_key}: DBusMenu {len(children)} items")
            gtk_menu = Gtk.Menu()
            self._populate_menu(gtk_menu, children, menu_iface)
            gtk_menu.show_all()
            return gtk_menu
        except dbus.DBusException as e:
            log.error(f"[SNI] {self.item_key}: DBusMenu failed: {e}")
            return None

    def _populate_menu(self, gtk_menu, items, menu_iface):
        for raw in items:
            item_id  = int(raw[0])
            props    = {str(k): v for k, v in dict(raw[1]).items()}
            children = raw[2]
            if not bool(props.get('visible', True)):
                continue
            if str(props.get('type', '')) == 'separator':
                gtk_menu.append(Gtk.SeparatorMenuItem())
                continue
            label    = str(props.get('label', f'item {item_id}')).replace('_', '', 1)
            gtk_item = Gtk.MenuItem(label=label)
            gtk_item.set_sensitive(bool(props.get('enabled', True)))
            if children:
                sub = Gtk.Menu()
                self._populate_menu(sub, children, menu_iface)
                gtk_item.set_submenu(sub)
            else:
                gtk_item.connect(
                    'activate',
                    lambda _, mid=item_id: self._menu_click(menu_iface, mid),
                )
            gtk_menu.append(gtk_item)

    def _menu_click(self, menu_iface, item_id):
        ts = int(GLib.get_monotonic_time() // 1000)
        log.info(f"[SNI] {self.item_key}: DBusMenu Event(id={item_id})")
        try:
            menu_iface.Event(
                dbus.Int32(item_id), dbus.String("clicked"),
                dbus.String(''), dbus.UInt32(ts),
            )
        except dbus.DBusException as e:
            log.error(f"[SNI] {self.item_key}: DBusMenu Event failed: {e}")

    def destroy(self):
        if self.button:
            self.button.destroy()
            self.button = None


# ═══════════════════════════════════════════════════════════════════════════════
# XAppItem — one Linux Mint XApp StatusIcon
# ═══════════════════════════════════════════════════════════════════════════════

class XAppItem:
    """
    Wraps one XApp.StatusIcon item: reads its properties via org.x.StatusIcon
    D-Bus interface and sends click events using ButtonPress / ButtonRelease.

    XApp icons are used by Linux Mint native apps: mintUpdate, nvidia-prime,
    mintreport, etc. They are NOT SNI items and do not go through the Watcher.
    """

    PANEL_POSITION_TOP = 2  # Gtk.PositionType.TOP

    def __init__(self, bus, bus_name, obj_path):
        self.bus_name = bus_name
        self.obj_path = obj_path
        self.button   = None
        self._bus     = bus

        log.info(f"[XApp] Connecting  bus={bus_name!r}  path={obj_path!r}")
        try:
            proxy        = bus.get_object(bus_name, obj_path)
            self._iface  = dbus.Interface(proxy, 'org.x.StatusIcon')
            self._props  = dbus.Interface(proxy, PROPS_IFACE)
        except dbus.DBusException as e:
            log.error(f"[XApp] {bus_name}: proxy failed: {e}")
            self._iface = self._props = None

        self._data = {}
        self._load_properties()

        # Subscribe to property changes for live icon updates
        try:
            bus.add_signal_receiver(
                self._on_props_changed,
                signal_name='PropertiesChanged',
                dbus_interface=PROPS_IFACE,
                bus_name=bus_name,
                path=obj_path,
            )
        except Exception as e:
            log.debug(f"[XApp] {bus_name}: can't subscribe PropertiesChanged: {e}")

    def _load_properties(self):
        if not self._props:
            return
        try:
            self._data = dict(self._props.GetAll('org.x.StatusIcon'))
            log.debug(f"[XApp] {self.bus_name} props: {sorted(self._data.keys())}")
        except dbus.DBusException as e:
            log.error(f"[XApp] {self.bus_name}: GetAll failed: {e}")

    def _prop(self, name, default=None):
        return self._data.get(name, default)

    @property
    def visible(self):
        return bool(self._prop('Visible', True))

    # ── Widget ─────────────────────────────────────────────────────────────────

    def build_button(self):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class("tray-btn")
        self.button = btn
        self._apply_icon()
        tooltip = str(self._prop('TooltipText') or self._prop('Name') or self.bus_name)
        btn.set_tooltip_text(tooltip)
        btn.connect('button-press-event', self._on_button_press)
        btn.show_all()
        return btn

    def _apply_icon(self):
        if not self.button:
            return
        child = self.button.get_child()
        if child:
            self.button.remove(child)
        icon_name = str(self._prop('IconName') or '').strip()
        img = Gtk.Image()
        if icon_name:
            img.set_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
            log.info(f"[XApp] {self.bus_name}: IconName={icon_name!r}")
        else:
            img.set_from_icon_name('application-x-executable-symbolic', Gtk.IconSize.SMALL_TOOLBAR)
        img.set_pixel_size(ICON_SIZE)
        self.button.add(img)
        self.button.show_all()

    def _on_props_changed(self, iface, changed, invalidated):
        changed = {str(k): v for k, v in dict(changed).items()}
        self._data.update(changed)
        log.info(f"[XApp] {self.bus_name}: props changed: {list(changed.keys())}")
        if 'IconName' in changed:
            GLib.idle_add(self._apply_icon)
        if 'TooltipText' in changed or 'Name' in changed:
            tip = str(self._prop('TooltipText') or self._prop('Name') or '')
            if self.button:
                self.button.set_tooltip_text(tip)
        if 'Visible' in changed and self.button:
            self.button.set_visible(bool(changed['Visible']))

    # ── Click handler ──────────────────────────────────────────────────────────

    def _on_button_press(self, btn, event):
        """
        XApp icons handle their own menu display when they receive
        ButtonPress + ButtonRelease with the correct button number.
        The panel_position arg (2 = TOP) tells the icon where to anchor its menu.
        """
        x      = int(event.x_root)
        y      = int(event.y_root)
        button = int(event.button)
        ts     = int(GLib.get_monotonic_time() // 1000) & 0xFFFFFFFF

        log.info(f"[XApp] {self.bus_name}: button {button} at ({x},{y})")
        try:
            self._iface.ButtonPress(
                dbus.Int32(x), dbus.Int32(y),
                dbus.UInt32(button), dbus.UInt32(ts),
                dbus.Int32(self.PANEL_POSITION_TOP),
            )
            self._iface.ButtonRelease(
                dbus.Int32(x), dbus.Int32(y),
                dbus.UInt32(button), dbus.UInt32(ts),
                dbus.Int32(self.PANEL_POSITION_TOP),
            )
        except dbus.DBusException as e:
            log.error(f"[XApp] {self.bus_name}: ButtonPress failed: {e}")
        return True

    def destroy(self):
        if self.button:
            self.button.destroy()
            self.button = None


# ═══════════════════════════════════════════════════════════════════════════════
# TrayApplet
# ═══════════════════════════════════════════════════════════════════════════════

class TrayApplet(Applet):
    """
    Hosts both SNI and XApp tray icons in one Gtk.Box.

    SNI icons appear first (pack_start), XApp icons after.
    """

    def __init__(self, panel):
        super().__init__(panel)
        self._sni_items   = {}  # item_key → TrayItem
        self._xapp_items  = {}  # "busname/path" → XAppItem

    def build(self):
        global ICON_SIZE
        ICON_SIZE = self.panel.config.get('icon_size', 16)

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)

        # SNI Watcher + Host (direct Python calls — no D-Bus self-call deadlock)
        self._watcher = StatusNotifierWatcher(
            self.panel.bus,
            on_item_added=self._on_sni_added,
            on_item_removed=self._on_sni_removed,
        )
        self._register_host()
        for key in list(self._watcher._items):
            self._on_sni_added(key)

        # XApp StatusIcon monitor
        self._xapp_monitor = XApp.StatusIconMonitor()
        self._xapp_monitor.connect('icon-added',   self._on_xapp_added)
        self._xapp_monitor.connect('icon-removed', self._on_xapp_removed)
        log.info("[Tray] XApp monitor started")

        return self._box

    def _register_host(self):
        try:
            self._host_bn = dbus.service.BusName(HOST_BUS_NAME, self.panel.bus)
            log.info(f"[Tray] Host bus name: {HOST_BUS_NAME}")
        except dbus.DBusException as e:
            log.error(f"[Tray] Host bus name failed: {e}")
        # Direct call — avoids D-Bus round-trip to ourselves before main loop starts
        self._watcher.RegisterStatusNotifierHost(HOST_BUS_NAME)
        log.info(f"[Tray] Host registered")

    # ── SNI callbacks ──────────────────────────────────────────────────────────

    def _on_sni_added(self, item_key):
        if item_key in self._sni_items:
            return
        item = TrayItem(self.panel.bus, item_key, debug=self.panel.debug)
        self._sni_items[item_key] = item
        self._box.pack_start(item.build_button(), False, False, 0)
        self._box.show_all()

    def _on_sni_removed(self, item_key):
        item = self._sni_items.pop(item_key, None)
        if item:
            if item.button:
                self._box.remove(item.button)
            item.destroy()

    # ── XApp callbacks ─────────────────────────────────────────────────────────

    def _on_xapp_added(self, monitor, proxy):
        key = proxy.get_name() + proxy.get_object_path()
        if key in self._xapp_items:
            return
        item = XAppItem(self.panel.bus, proxy.get_name(), proxy.get_object_path())
        self._xapp_items[key] = item
        if not item.visible:
            log.debug(f"[Tray] XApp {proxy.get_name()} invisible, hiding button")
        btn = item.build_button()
        btn.set_visible(item.visible)
        self._box.pack_start(btn, False, False, 0)
        self._box.show_all()

    def _on_xapp_removed(self, monitor, proxy):
        key = proxy.get_name() + proxy.get_object_path()
        item = self._xapp_items.pop(key, None)
        if item:
            if item.button:
                self._box.remove(item.button)
            item.destroy()

    def destroy(self):
        for item in list(self._sni_items.values()):
            item.destroy()
        for item in list(self._xapp_items.values()):
            item.destroy()


# ── Helper ─────────────────────────────────────────────────────────────────────

def _btn_screen_pos(btn):
    gdk_win = btn.get_window()
    if gdk_win:
        alloc = btn.get_allocation()
        rx, ry = gdk_win.get_root_coords(alloc.x, alloc.y)
        return int(rx), int(ry)
    return 0, 0
