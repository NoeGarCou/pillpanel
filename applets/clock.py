"""
applets/clock.py — clock + Cinnamon-style calendar popup.

Popup layout:
  ┌─────────────────────┬──────────────────────────────┐
  │ Domingo,            │       Domingo                │
  │ 31 de mayo de 2026  │   31 de mayo de 2026         │
  │ ─────────────────── │  ◀ Mayo ▶  ◀ 2026 ▶          │
  │                     │  lun mar mié jue vie sáb dom  │
  │    [calendar icon]  │   27  28  29  30   1   2   3  │
  │      No Events      │   ...                         │
  │                     │  Date and Time Settings       │
  └─────────────────────┴──────────────────────────────┘
"""

import logging
import subprocess
from datetime import date, timedelta, datetime

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from .base  import Applet
from .popup import PanelPopup

log = logging.getLogger('pillpanel.clock')

# Column indices for weekend days (0 = Monday … 6 = Sunday)
_WEEKEND = {5, 6}


def _month_weeks(year: int, month: int):
    """
    Return exactly 6 rows of (date, is_current_month) pairs.
    Each row covers Monday→Sunday, padding with adjacent-month days as needed.
    """
    first   = date(year, month, 1)
    current = first - timedelta(days=first.weekday())   # rewind to Monday
    weeks   = []
    while len(weeks) < 6:
        week = [(current + timedelta(days=i),
                 (current + timedelta(days=i)).month == month)
                for i in range(7)]
        weeks.append(week)
        current += timedelta(weeks=1)
    return weeks


# ═══════════════════════════════════════════════════════════════════════════════
# ClockApplet
# ═══════════════════════════════════════════════════════════════════════════════

class ClockApplet(Applet):
    """Shows date + time; click opens CalendarPopup."""

    def build(self):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class('panel-btn')

        self._label = Gtk.Label()
        # Pin width to the widest possible string so the center section
        # doesn't reflow when day/month names change length.
        self._label.set_width_chars(46)
        self._label.set_xalign(0.5)
        btn.add(self._label)

        self._update()
        self._timer = GLib.timeout_add(1000, self._update)

        self._calendar = CalendarPopup()
        btn.connect('clicked', lambda b: self._calendar.toggle(b))
        return btn

    def _update(self):
        now = datetime.now()
        self._label.set_markup(
            f'<span foreground="white" font="10">'
            f'{now.strftime("%A, %-d de %B de %Y").capitalize()}  <b>{now.strftime("%H:%M")}</b>'
            f'</span>'
        )
        return True

    def destroy(self):
        GLib.source_remove(self._timer)


# ═══════════════════════════════════════════════════════════════════════════════
# CalendarPopup
# ═══════════════════════════════════════════════════════════════════════════════

class CalendarPopup:

    def __init__(self):
        self._today         = date.today()
        self._view_year     = self._today.year
        self._view_month    = self._today.month
        self._selected_day  = self._today

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        # Minimum total width; right panel expands to fill anything beyond 220 px.
        outer.set_size_request(660, -1)

        outer.pack_start(self._build_left(),  False, False, 0)
        outer.pack_start(self._build_right(), True,  True,  0)

        self._popup = PanelPopup(outer, center=True)

    def toggle(self, btn):
        self._popup.toggle(btn)

    # ── Left panel — date header + "No Events" ─────────────────────────────────

    def _build_left(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_size_request(360, 300)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(16)
        box.set_margin_end(12)

        # "Domingo, 31 de mayo de 2026" — updated on day selection
        self._left_date_lbl = Gtk.Label()
        self._left_date_lbl.set_halign(Gtk.Align.START)
        self._left_date_lbl.set_max_width_chars(33)
        self._update_left_date(self._today)
        box.pack_start(self._left_date_lbl, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class('cal-sep')
        box.pack_start(sep, False, False, 0)

        # Centred placeholder — clicking opens the calendar app (same as Cinnamon)
        ph_btn = Gtk.Button()
        ph_btn.set_relief(Gtk.ReliefStyle.NONE)
        ph_btn.set_focus_on_click(False)
        ph_btn.get_style_context().add_class('cal-events-btn')
        ph_btn.set_vexpand(True)
        ph_btn.connect('clicked', lambda _: self._open_calendar())

        ph = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ph.set_valign(Gtk.Align.CENTER)
        ph.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name('x-office-calendar-symbolic', Gtk.IconSize.DIALOG)
        icon.get_style_context().add_class('cal-dim')

        lbl = Gtk.Label(label='No Events')
        lbl.get_style_context().add_class('cal-dim')

        ph.pack_start(icon, False, False, 0)
        ph.pack_start(lbl,  False, False, 0)
        ph_btn.add(ph)
        box.pack_start(ph_btn, True, True, 0)

        return box

    # ── Right panel — calendar grid ────────────────────────────────────────────

    def _build_right(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(14)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(16)
        self._right_box = box

        # Large day name header
        self._hdr_day = Gtk.Label()
        self._hdr_day.set_halign(Gtk.Align.CENTER)
        box.pack_start(self._hdr_day, False, False, 0)

        # Subtitle: full date
        self._hdr_full = Gtk.Label()
        self._hdr_full.set_halign(Gtk.Align.CENTER)
        box.pack_start(self._hdr_full, False, False, 2)

        # Month / year navigation
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        nav.set_halign(Gtk.Align.CENTER)
        nav.set_margin_top(10)
        nav.set_margin_bottom(4)

        self._month_lbl = Gtk.Label()
        self._month_lbl.get_style_context().add_class('cal-nav-label')
        self._month_lbl.set_size_request(96, -1)
        self._month_lbl.set_halign(Gtk.Align.CENTER)

        self._year_lbl  = Gtk.Label()
        self._year_lbl.get_style_context().add_class('cal-nav-label')
        self._year_lbl.set_size_request(58, -1)
        self._year_lbl.set_halign(Gtk.Align.CENTER)

        for w in [self._nav_btn('◀', self._prev_month), self._month_lbl,
                  self._nav_btn('▶', self._next_month),
                  self._nav_btn('◀', self._prev_year),  self._year_lbl,
                  self._nav_btn('▶', self._next_year)]:
            nav.pack_start(w, False, False, 0)

        box.pack_start(nav, False, False, 0)

        # Day-of-week header row
        dow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        dow_row.set_margin_bottom(2)
        for col in range(7):
            ref  = date(2024, 1, 1 + col)          # 2024-01-01 is Monday
            name = ref.strftime('%a').rstrip('.').lower()
            lbl  = Gtk.Label(label=name)
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.set_hexpand(True)
            lbl.get_style_context().add_class('cal-dow')
            if col in _WEEKEND:
                lbl.get_style_context().add_class('cal-weekend')
            dow_row.pack_start(lbl, True, True, 0)
        box.pack_start(dow_row, False, False, 0)

        # Grid container — replaced on every month change
        self._grid_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self._grid_slot, False, False, 0)

        # Bottom settings link
        settings = Gtk.Button(label='Date and Time Settings')
        settings.set_relief(Gtk.ReliefStyle.NONE)
        settings.get_style_context().add_class('cal-settings-btn')
        settings.set_margin_top(8)
        settings.set_halign(Gtk.Align.START)
        settings.connect('clicked', lambda _: self._open_settings())
        box.pack_start(settings, False, False, 0)

        self._refresh_header()
        self._refresh_grid()
        return box

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _nav_btn(self, symbol, cb):
        btn = Gtk.Button(label=symbol)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class('cal-nav-btn')
        btn.connect('clicked', lambda _: cb())
        return btn

    def _prev_month(self):
        self._view_month -= 1
        if self._view_month < 1:
            self._view_month, self._view_year = 12, self._view_year - 1
        self._refresh_header(); self._refresh_grid()

    def _next_month(self):
        self._view_month += 1
        if self._view_month > 12:
            self._view_month, self._view_year = 1, self._view_year + 1
        self._refresh_header(); self._refresh_grid()

    def _prev_year(self):
        self._view_year -= 1
        self._refresh_header(); self._refresh_grid()

    def _next_year(self):
        self._view_year += 1
        self._refresh_header(); self._refresh_grid()

    # ── Rendering ──────────────────────────────────────────────────────────────

    def _update_left_date(self, d: date):
        self._left_date_lbl.set_markup(
            f'<span foreground="white" font="13">'
            f'<b>{d.strftime("%A, %-d de %B de %Y").capitalize()}</b>'
            f'</span>'
        )

    def _refresh_header(self):
        selected_in_view = (self._selected_day.year  == self._view_year and
                            self._selected_day.month == self._view_month)
        ref = self._selected_day if selected_in_view else date(self._view_year, self._view_month, 1)

        self._hdr_day.set_markup(
            f'<span foreground="white" font="18" font_weight="bold">'
            f'{ref.strftime("%A").capitalize()}'
            f'</span>'
        )
        # Pango does not accept rgba() in foreground; use foreground + alpha instead.
        self._hdr_full.set_markup(
            f'<span foreground="white" alpha="45875" font="13">'
            f'{ref.strftime("%-d de %B de %Y").capitalize()}'
            f'</span>'
        )
        month_str = date(self._view_year, self._view_month, 1).strftime('%B').capitalize()
        self._month_lbl.set_markup(
            f'<span foreground="white" font="13" font_weight="bold">{month_str}</span>'
        )
        self._year_lbl.set_markup(
            f'<span foreground="white" font="13" font_weight="bold">{self._view_year}</span>'
        )

    def _refresh_grid(self):
        for child in self._grid_slot.get_children():
            self._grid_slot.remove(child)

        grid = Gtk.Grid()
        grid.set_row_spacing(2)
        grid.set_column_spacing(0)
        grid.set_column_homogeneous(True)
        grid.set_hexpand(True)

        for row, week in enumerate(_month_weeks(self._view_year, self._view_month)):
            for col, (d, in_month) in enumerate(week):
                btn = Gtk.Button(label=str(d.day))
                btn.set_relief(Gtk.ReliefStyle.NONE)
                btn.set_focus_on_click(False)
                btn.set_size_request(32, 36)
                btn.set_hexpand(True)
                btn.get_style_context().add_class('cal-day-btn')

                if d == self._today:
                    btn.get_style_context().add_class('cal-today')
                elif d == self._selected_day:
                    btn.get_style_context().add_class('cal-selected')
                elif not in_month:
                    btn.get_style_context().add_class('cal-other-month')

                btn.connect('clicked', lambda _, d=d: self._on_day_clicked(d))
                grid.attach(btn, col, row, 1, 1)

        self._grid_slot.pack_start(grid, True, True, 0)
        self._grid_slot.show_all()

    def _on_day_clicked(self, d: date):
        self._selected_day  = d
        # Navigate to the month containing the clicked day (handles adjacent-month days).
        self._view_year     = d.year
        self._view_month    = d.month
        self._update_left_date(d)
        self._refresh_header()
        self._refresh_grid()

    def _open_calendar(self):
        for cmd in (['gnome-calendar'],
                    ['xdg-open', 'calendar://']):
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue

    def _open_settings(self):
        for cmd in (['cinnamon-settings', 'calendar'],
                    ['gnome-control-center', 'datetime']):
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue
