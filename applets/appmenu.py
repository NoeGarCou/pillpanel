"""applets/appmenu.py — system menu + PillPanel preferences."""

import importlib.metadata as _imeta
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import urllib.request

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from .base  import Applet
from .popup import PanelPopup

log = logging.getLogger('pillpanel.appmenu')

_GITHUB_API  = 'https://api.github.com/repos/NoeGarCou/pillpanel/commits/main'
_GITHUB_PKG  = 'git+https://github.com/NoeGarCou/pillpanel.git'


def _installed_commit() -> 'str | None':
    """Return the full git SHA the installed package was built from, or None."""
    try:
        raw = _imeta.distribution('pillpanel').read_text('direct_url.json')
        if raw:
            return json.loads(raw).get('vcs_info', {}).get('commit_id')
    except Exception:
        return None


def _latest_commit() -> str:
    """Fetch the latest commit SHA on main from GitHub (raises on failure)."""
    req = urllib.request.Request(
        _GITHUB_API,
        headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'pillpanel'},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())['sha']


# Curated list — only entries whose command exists on PATH are shown.
_SYSTEM_APPS = [
    ('nemo',                 'Files',             'system-file-manager'),
    ('gnome-terminal',       'Terminal',          'utilities-terminal'),
    ('cinnamon-settings',    'System Settings',   'preferences-system'),
    ('gnome-system-monitor', 'System Monitor',    'utilities-system-monitor'),
    ('mintinstall',          'Software Manager',  'system-software-install'),
    ('timeshift-gtk',        'Timeshift',         'timeshift-gtk'),
    ('gparted',              'GParted',           'gparted'),
]


class AppMenuApplet(Applet):
    """System menu — curated app launchers + PillPanel Preferences."""

    def build(self):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class('panel-btn')
        btn.get_style_context().add_class('appmenu-btn')
        btn.set_tooltip_text('System menu')

        self._btn_icon = Gtk.Image.new_from_icon_name(
            'linuxmint-logo-ring-symbolic', Gtk.IconSize.SMALL_TOOLBAR
        )
        btn.add(self._btn_icon)

        self._popup = self._build_popup()
        btn.connect('clicked', lambda b: self._popup.toggle(b))
        return btn

    def after_icon_size(self):
        px = self.panel.config.get('appmenu_btn_icon_size', 16)
        self._btn_icon.set_pixel_size(px)

    def _build_popup(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(230, -1)
        root.set_margin_top(4)
        root.set_margin_bottom(6)

        icon_px = self.panel.config.get('appmenu_icon_size', 20)
        font_px = self.panel.config.get('appmenu_font_size', 13)

        for cmd, label, icon_name in _SYSTEM_APPS:
            if not shutil.which(cmd):
                continue
            root.pack_start(
                _app_row(label, icon_name, lambda c=cmd: self._launch(c),
                         icon_px=icon_px, font_px=font_px),
                False, False, 0,
            )

        sep = Gtk.Separator()
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        root.pack_start(sep, False, False, 0)

        root.pack_start(
            _app_row('Panel Preferences', 'gnome-settings',
                     self._open_preferences, icon_px=icon_px, font_px=font_px),
            False, False, 0,
        )
        return PanelPopup(root)

    def _launch(self, cmd):
        try:
            subprocess.Popen([cmd])
            self._popup.hide()
        except Exception as e:
            log.error(f"[SystemMenu] Launch '{cmd}': {e}")

    def _open_preferences(self):
        self._popup.hide()
        PreferencesWindow(self.panel).present()


# ═══════════════════════════════════════════════════════════════════════════════
# PreferencesWindow
# ═══════════════════════════════════════════════════════════════════════════════

class PreferencesWindow:
    """
    Settings window for PillPanel.

    Colour changes are applied live (no restart needed).
    Panel height and icon size take effect after Save & Restart.
    """

    _instance = None   # keep only one open at a time

    def __new__(cls, panel):
        if cls._instance and cls._instance._win.get_visible():
            cls._instance._win.present()
            return cls._instance
        obj = super().__new__(cls)
        cls._instance = obj
        return obj

    def __init__(self, panel):
        self._panel = panel
        self._build()

    def present(self):
        self._win.present()

    def _build(self):
        win = Gtk.Window(title='PillPanel Preferences')
        win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        win.set_resizable(False)
        win.set_keep_above(True)
        win.set_border_width(18)
        win.set_default_size(380, -1)
        win.connect('delete-event', lambda w, e: w.hide() or True)
        self._win = win

        cfg  = self._panel.config
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        win.add(vbox)

        # ── Appearance ─────────────────────────────────────────────────────────
        vbox.pack_start(_section_label('Appearance'), False, False, 0)

        self._bg_btn = _color_button(
            cfg.get('pill_bg', 'rgba(30, 30, 36, 0.863)'),
            'Background colour',
        )
        vbox.pack_start(_pref_row('Background colour', self._bg_btn), False, False, 0)

        self._border_btn = _color_button(
            cfg.get('pill_stroke', 'rgba(255, 255, 255, 0.039)'),
            'Border colour',
        )
        vbox.pack_start(_pref_row('Border colour', self._border_btn), False, False, 0)

        # ── Layout ─────────────────────────────────────────────────────────────
        vbox.pack_start(_section_label('Layout'), False, False, 0)

        self._height_spin = Gtk.SpinButton.new_with_range(24, 64, 2)
        self._height_spin.set_value(cfg.get('outer_height', 36))
        vbox.pack_start(_pref_row('Panel height (px)', self._height_spin), False, False, 0)

        self._icon_spin = Gtk.SpinButton.new_with_range(12, 32, 2)
        self._icon_spin.set_value(cfg.get('icon_size', 16))
        vbox.pack_start(_pref_row('Icon size (px)', self._icon_spin), False, False, 0)

        self._desktop_icon_spin = Gtk.SpinButton.new_with_range(12, 48, 2)
        self._desktop_icon_spin.set_value(cfg.get('show_desktop_icon_size', 16))
        vbox.pack_start(_pref_row('Show Desktop icon (px)', self._desktop_icon_spin), False, False, 0)

        self._appmenu_btn_icon_spin = Gtk.SpinButton.new_with_range(12, 48, 2)
        self._appmenu_btn_icon_spin.set_value(cfg.get('appmenu_btn_icon_size', 16))
        vbox.pack_start(_pref_row('App menu button icon (px)', self._appmenu_btn_icon_spin), False, False, 0)

        self._appmenu_icon_spin = Gtk.SpinButton.new_with_range(12, 48, 2)
        self._appmenu_icon_spin.set_value(cfg.get('appmenu_icon_size', 20))
        vbox.pack_start(_pref_row('App menu icon size (px)', self._appmenu_icon_spin), False, False, 0)

        self._appmenu_font_spin = Gtk.SpinButton.new_with_range(8, 24, 1)
        self._appmenu_font_spin.set_value(cfg.get('appmenu_font_size', 13))
        vbox.pack_start(_pref_row('App menu font size (px)', self._appmenu_font_spin), False, False, 0)

        notice = Gtk.Label(label='Height and icon size changes require a restart.')
        notice.get_style_context().add_class('cal-dim')
        notice.set_halign(Gtk.Align.START)
        vbox.pack_start(notice, False, False, 0)

        # ── Updates ────────────────────────────────────────────────────────────
        vbox.pack_start(_section_label('Updates'), False, False, 0)

        update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._update_btn = Gtk.Button(label='Check for updates')
        self._update_btn.connect('clicked', self._on_update_btn)
        update_row.pack_start(self._update_btn, False, False, 0)
        self._update_lbl = Gtk.Label(label='')
        self._update_lbl.get_style_context().add_class('cal-dim')
        self._update_lbl.set_halign(Gtk.Align.START)
        update_row.pack_start(self._update_lbl, True, True, 0)
        vbox.pack_start(update_row, False, False, 0)
        self._update_state = 'idle'  # idle | checking | available | updating

        # ── Buttons ────────────────────────────────────────────────────────────
        sep = Gtk.Separator()
        sep.set_margin_top(4)
        vbox.pack_start(sep, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)

        apply_btn = Gtk.Button(label='Apply colours')
        apply_btn.connect('clicked', self._on_apply)
        btn_row.pack_start(apply_btn, False, False, 0)

        restart_btn = Gtk.Button(label='Save & Restart')
        restart_btn.connect('clicked', self._on_save_restart)
        btn_row.pack_start(restart_btn, False, False, 0)

        close_btn = Gtk.Button(label='Close')
        close_btn.connect('clicked', lambda _: self._win.hide())
        btn_row.pack_start(close_btn, False, False, 0)

        vbox.pack_start(btn_row, False, False, 0)
        win.show_all()

    # ── Handlers ───────────────────────────────────────────────────────────────

    def _on_apply(self, _btn):
        """Apply colour changes live; save all settings to disk."""
        self._panel.config['pill_bg']     = _rgba_to_css(self._bg_btn.get_rgba())
        self._panel.config['pill_stroke'] = _rgba_to_css(self._border_btn.get_rgba())
        self._panel.reload_css()
        _save(self._panel.config)

    def _on_save_restart(self, _btn):
        """Save all settings (including height/icon) then restart the process."""
        self._panel.config['pill_bg']                = _rgba_to_css(self._bg_btn.get_rgba())
        self._panel.config['pill_stroke']            = _rgba_to_css(self._border_btn.get_rgba())
        self._panel.config['outer_height']           = int(self._height_spin.get_value())
        self._panel.config['icon_size']              = int(self._icon_spin.get_value())
        self._panel.config['show_desktop_icon_size'] = int(self._desktop_icon_spin.get_value())
        self._panel.config['appmenu_btn_icon_size']  = int(self._appmenu_btn_icon_spin.get_value())
        self._panel.config['appmenu_icon_size']      = int(self._appmenu_icon_spin.get_value())
        self._panel.config['appmenu_font_size']      = int(self._appmenu_font_spin.get_value())
        _save(self._panel.config)
        self._win.hide()
        subprocess.Popen([sys.executable] + sys.argv)
        Gtk.main_quit()

    # ── Update handlers ────────────────────────────────────────────────────────

    def _on_update_btn(self, _btn):
        if self._update_state == 'available':
            self._run_update()
        else:
            self._run_check()

    def _run_check(self):
        self._update_state = 'checking'
        self._update_btn.set_sensitive(False)
        self._update_lbl.set_text('Checking...')
        threading.Thread(target=self._check_thread, daemon=True).start()

    def _check_thread(self):
        try:
            latest    = _latest_commit()
            installed = _installed_commit()
            up_to_date = bool(installed and installed == latest)
            GLib.idle_add(self._after_check, up_to_date, latest)
        except Exception as e:
            GLib.idle_add(self._after_check, None, str(e))

    def _after_check(self, up_to_date, info):
        if up_to_date is True:
            self._update_state = 'idle'
            self._update_lbl.set_text('Already up to date.')
            self._update_btn.set_label('Check for updates')
        elif up_to_date is False:
            self._update_state = 'available'
            self._update_lbl.set_text(f'Update available  ({info[:7]})')
            self._update_btn.set_label('Update now')
        else:
            self._update_state = 'idle'
            self._update_lbl.set_text('Could not check for updates.')
            self._update_btn.set_label('Check for updates')
            log.warning(f'[Updates] Check failed: {info}')
        self._update_btn.set_sensitive(True)
        return False

    def _run_update(self):
        self._update_state = 'updating'
        self._update_btn.set_sensitive(False)
        self._update_lbl.set_text('Updating...')
        threading.Thread(target=self._update_thread, daemon=True).start()

    def _update_thread(self):
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--user',
                 '--break-system-packages', '--force-reinstall', _GITHUB_PKG],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                GLib.idle_add(self._after_update, True, None)
            else:
                GLib.idle_add(self._after_update, False, result.stderr[-300:])
        except Exception as e:
            GLib.idle_add(self._after_update, False, str(e))

    def _after_update(self, success, error):
        self._update_state = 'idle'
        self._update_btn.set_label('Check for updates')
        self._update_btn.set_sensitive(True)
        if success:
            self._update_lbl.set_text('Updated. Click Save & Restart to apply.')
        else:
            self._update_lbl.set_text('Update failed — check logs.')
            log.error(f'[Updates] pip failed: {error}')
        return False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _app_row(label_text, icon_name, callback, icon_px=20, font_px=13):
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_focus_on_click(False)
    btn.get_style_context().add_class('net-row-btn')

    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    hbox.set_margin_start(12)
    hbox.set_margin_end(12)
    hbox.set_margin_top(6)
    hbox.set_margin_bottom(6)

    img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
    img.set_pixel_size(icon_px)
    hbox.pack_start(img, False, False, 0)

    lbl = Gtk.Label(label=label_text)
    lbl.set_halign(Gtk.Align.START)
    lbl.get_style_context().add_class('panel-label')
    _apply_font_size(lbl, font_px)
    hbox.pack_start(lbl, True, True, 0)
    btn.add(hbox)
    btn.connect('clicked', lambda _: callback())
    return btn


def _apply_font_size(widget, px: int):
    """Override font size on a single widget via a high-priority CSS provider."""
    provider = Gtk.CssProvider()
    provider.load_from_data(f'* {{ font-size: {px}px; }}'.encode())
    widget.get_style_context().add_provider(
        provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

def _section_label(text):
    lbl = Gtk.Label()
    lbl.set_markup(f'<b>{text}</b>')
    lbl.set_halign(Gtk.Align.START)
    return lbl

def _pref_row(label_text, widget):
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    lbl = Gtk.Label(label=label_text)
    lbl.set_halign(Gtk.Align.START)
    lbl.set_size_request(200, -1)
    row.pack_start(lbl, False, False, 0)
    row.pack_start(widget, False, False, 0)
    return row

def _color_button(css_str: str, title: str) -> Gtk.ColorButton:
    btn = Gtk.ColorButton()
    btn.set_use_alpha(True)
    btn.set_title(title)
    rgba = Gdk.RGBA()
    if not rgba.parse(css_str):
        rgba.parse('rgba(30,30,36,0.86)')
    btn.set_rgba(rgba)
    return btn

def _save(cfg: dict):
    import os
    path = os.path.expanduser('~/.config/pillpanel/config.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)
    log.info(f"[Prefs] Config saved to {path}")

def _rgba_to_css(rgba: Gdk.RGBA) -> str:
    r = int(rgba.red   * 255)
    g = int(rgba.green * 255)
    b = int(rgba.blue  * 255)
    return f'rgba({r}, {g}, {b}, {rgba.alpha:.3f})'
