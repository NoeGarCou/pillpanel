"""applets/mintyai.py — Minty AI chat applet for PillPanel."""

import json
import logging
import threading
import urllib.request

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

from .base  import Applet
from .popup import PanelPopup

log = logging.getLogger('pillpanel.mintyai')

# ── Config ─────────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = 'http://localhost:11434/v1'
OLLAMA_API_BASE = 'http://localhost:11434/api'
DEFAULT_MODEL   = 'qwen2.5:14b'
KEEP_ALIVE      = '15m'
POPUP_WIDTH     = 400
CHAT_HEIGHT     = 460

SYSTEM_PROMPT = (
    "You are Minty, a concise assistant living in the user's Linux panel. "
    "Answer in 1–3 sentences unless asked for detail. "
    "Prefer practical, actionable answers. "
    "If you're not sure about something, say so rather than guess. "
    "For Linux commands, show the command first, then a brief explanation. "
    "Be friendly but don't waste the user's time on pleasantries."
)

_CSS = b"""
.minty-user {
    background-color: rgba(21, 101, 192, 0.85);
    border-radius: 10px;
    padding: 7px 11px;
}
.minty-asst {
    background-color: rgba(255, 255, 255, 0.07);
    border-radius: 10px;
    padding: 7px 11px;
}
.minty-msg {
    color: white;
    font-size: 13px;
}
.minty-status {
    color: rgba(255,255,255,0.42);
    font-size: 11px;
}
button.minty-send-btn {
    background: rgba(21, 101, 192, 0.85);
    border: none;
    box-shadow: none;
    border-radius: 16px;
    color: white;
    font-size: 13px;
    padding: 4px 14px;
    min-height: 0;
}
button.minty-send-btn:hover {
    background: rgba(25, 118, 210, 0.95);
}
button.minty-cancel-btn {
    background: rgba(175, 35, 35, 0.80);
    border: none;
    box-shadow: none;
    border-radius: 16px;
    color: white;
    font-size: 13px;
    padding: 4px 14px;
    min-height: 0;
}
button.minty-cancel-btn:hover {
    background: rgba(198, 40, 40, 0.95);
}
button.minty-new-btn {
    background: transparent;
    border: 1px solid rgba(255,255,255,0.16);
    box-shadow: none;
    border-radius: 12px;
    color: rgba(255,255,255,0.60);
    font-size: 11px;
    padding: 2px 10px;
    min-height: 20px;
    min-width: 0;
}
button.minty-new-btn:hover {
    background: rgba(255,255,255,0.07);
    color: rgba(255,255,255,0.85);
}
"""

_MINT_GREEN = Gdk.RGBA(135 / 255, 192 / 255, 80 / 255, 1.0)
_KEEP_ALIVE_SECS = 15 * 60

_css_installed = False


def _install_css():
    global _css_installed
    if _css_installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _css_installed = True


# ── Ollama model list (called from background thread) ─────────────────────────

def _fetch_models() -> list:
    req = urllib.request.Request(f'{OLLAMA_API_BASE}/tags')
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return [m['name'] for m in data.get('models', [])]


# ══════════════════════════════════════════════════════════════════════════════
# Applet
# ══════════════════════════════════════════════════════════════════════════════

class MintyAIApplet(Applet):
    """Minty AI — streaming chat via Ollama, shown in a panel popup."""

    def build(self):
        _install_css()
        self._chat       = _ChatWidget(self.panel)
        self._popup      = PanelPopup(self._chat.root)
        self._warm_timer = None

        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class('panel-btn')
        btn.get_style_context().add_class('appmenu-btn')
        btn.set_tooltip_text('Minty AI')

        self._btn_icon = Gtk.Image.new_from_icon_name(
            'linuxmint-logo-ring-symbolic', Gtk.IconSize.SMALL_TOOLBAR
        )
        btn.add(self._btn_icon)
        btn.connect('clicked', self._on_click)
        return btn

    def after_icon_size(self):
        px = self.panel.config.get('appmenu_btn_icon_size', 16)
        self._btn_icon.set_pixel_size(px)

    def _on_click(self, btn):
        if not self._popup._visible:
            threading.Thread(target=self._do_warm, daemon=True).start()
            GLib.idle_add(self._chat.entry.grab_focus)
        self._popup.toggle(btn)

    # ── Warm-up ───────────────────────────────────────────────────────────────

    def _do_warm(self):
        try:
            data = json.dumps({
                'model': self._chat.current_model,
                'prompt': '',
                'keep_alive': KEEP_ALIVE,
            }).encode()
            req = urllib.request.Request(
                f'{OLLAMA_API_BASE}/generate',
                data=data,
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=30)
            GLib.idle_add(self._set_icon_warm)
        except Exception:
            pass

    def _set_icon_warm(self):
        self._btn_icon.override_color(Gtk.StateFlags.NORMAL, _MINT_GREEN)
        if self._warm_timer:
            GLib.source_remove(self._warm_timer)
        self._warm_timer = GLib.timeout_add_seconds(
            _KEEP_ALIVE_SECS, self._on_warm_expired
        )
        return False

    def _on_warm_expired(self):
        self._btn_icon.override_color(Gtk.StateFlags.NORMAL, None)
        self._warm_timer = None
        return False

    def destroy(self):
        self._chat.cancel()
        if self._warm_timer:
            GLib.source_remove(self._warm_timer)
            self._warm_timer = None


# ══════════════════════════════════════════════════════════════════════════════
# Chat widget
# ══════════════════════════════════════════════════════════════════════════════

class _ChatWidget:
    """Self-contained chat UI. Instantiated once; lives on the applet."""

    def __init__(self, panel):
        self._panel         = panel
        self._model         = DEFAULT_MODEL
        self._system_prompt = SYSTEM_PROMPT
        self._history       = [{'role': 'system', 'content': self._system_prompt}]
        self._is_sending    = False
        self._is_cancelled  = False
        self._asst_lbl      = None   # Gtk.Label being streamed into
        self._asst_text     = ''     # accumulated text for the current response
        self.root           = self._build()

    # ── Public ────────────────────────────────────────────────────────────────

    @property
    def current_model(self) -> str:
        return self._model

    def cancel(self):
        self._is_cancelled = True

    def new_chat(self):
        self.cancel()
        self._history = [{'role': 'system', 'content': self._system_prompt}]
        for row in self._list_box.get_children():
            self._list_box.remove(row)
        self._status_lbl.set_text('')
        self._set_sending(False)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(POPUP_WIDTH, -1)

        # ── Header: settings button + new-chat button ───────────────────────
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr.set_margin_start(10)
        hdr.set_margin_end(10)
        hdr.set_margin_top(8)
        hdr.set_margin_bottom(6)

        settings_btn = Gtk.Button()
        settings_btn.set_relief(Gtk.ReliefStyle.NONE)
        settings_btn.get_style_context().add_class('minty-new-btn')
        settings_btn.set_tooltip_text('Settings')
        settings_btn.add(Gtk.Image.new_from_icon_name(
            'preferences-system-symbolic', Gtk.IconSize.MENU
        ))
        settings_btn.connect('clicked', lambda _: _SettingsWindow.open(self))
        hdr.pack_start(settings_btn, False, False, 0)

        new_btn = Gtk.Button(label='New chat')
        new_btn.set_relief(Gtk.ReliefStyle.NONE)
        new_btn.get_style_context().add_class('minty-new-btn')
        new_btn.connect('clicked', lambda _: self.new_chat())
        hdr.pack_end(new_btn, False, False, 0)

        root.pack_start(hdr, False, False, 0)
        root.pack_start(Gtk.Separator(), False, False, 0)

        # ── Message list ────────────────────────────────────────────────────
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0)
        )

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_size_request(-1, CHAT_HEIGHT)
        self._scroll.add(self._list_box)
        root.pack_start(self._scroll, True, True, 0)

        # ── Status line ─────────────────────────────────────────────────────
        self._status_lbl = Gtk.Label(label='')
        self._status_lbl.set_halign(Gtk.Align.START)
        self._status_lbl.get_style_context().add_class('minty-status')
        self._status_lbl.set_margin_start(12)
        self._status_lbl.set_margin_end(12)
        self._status_lbl.set_margin_top(3)
        root.pack_start(self._status_lbl, False, False, 0)

        # ── Input row ───────────────────────────────────────────────────────
        inp = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inp.set_margin_start(10)
        inp.set_margin_end(10)
        inp.set_margin_top(4)
        inp.set_margin_bottom(10)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text('Ask Minty…')
        self.entry.connect('activate', self._on_send)
        inp.pack_start(self.entry, True, True, 0)

        self._send_btn = Gtk.Button(label='Send')
        self._send_btn.get_style_context().add_class('minty-send-btn')
        self._send_btn.connect('clicked', self._on_send)
        inp.pack_end(self._send_btn, False, False, 0)

        self._cancel_btn = Gtk.Button(label='Stop')
        self._cancel_btn.get_style_context().add_class('minty-cancel-btn')
        self._cancel_btn.connect('clicked', lambda _: self.cancel())
        self._cancel_btn.set_no_show_all(True)
        inp.pack_end(self._cancel_btn, False, False, 0)

        root.pack_end(inp, False, False, 0)

        return root

    # ── Send / stream ─────────────────────────────────────────────────────────

    def _on_send(self, _w):
        if self._is_sending:
            return
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text('')
        self._history.append({'role': 'user', 'content': text})
        self._add_bubble(text, is_user=True)
        self._asst_text    = ''
        self._asst_lbl     = self._add_bubble('', is_user=False)
        self._is_cancelled = False
        self._set_sending(True)
        self._status_lbl.set_text('Thinking…')
        threading.Thread(
            target=self._stream_thread,
            args=(list(self._history), self.current_model),
            daemon=True,
        ).start()

    def _stream_thread(self, messages: list, model: str):
        try:
            try:
                import openai
            except ImportError:
                GLib.idle_add(
                    self._on_error,
                    'openai package missing.\nRun: pip install openai',
                )
                return

            client = openai.OpenAI(base_url=OLLAMA_BASE_URL, api_key='ollama')
            stream = client.chat.completions.create(
                model=model, messages=messages, stream=True
            )
            for chunk in stream:
                if self._is_cancelled:
                    stream.close()
                    break
                delta = chunk.choices[0].delta.content
                if delta:
                    GLib.idle_add(self._on_token, delta)

            GLib.idle_add(self._on_done)

        except Exception as exc:
            msg = str(exc)
            if 'Connection refused' in msg or 'connect' in msg.lower():
                msg = 'Ollama is not running.\nStart it with: ollama serve'
            GLib.idle_add(self._on_error, msg)

    # ── GTK-thread callbacks ───────────────────────────────────────────────────

    def _on_token(self, token: str):
        self._asst_text += token
        if self._asst_lbl:
            self._asst_lbl.set_text(self._asst_text)
        self._scroll_bottom()
        return False

    def _on_done(self):
        if self._asst_text:
            self._history.append(
                {'role': 'assistant', 'content': self._asst_text}
            )
        self._status_lbl.set_text('')
        self._set_sending(False)
        self._asst_lbl = None
        self._scroll_bottom()
        return False

    def _on_error(self, msg: str):
        # Remove the empty assistant bubble if nothing was streamed
        if not self._asst_text and self._asst_lbl:
            # walk: label → bubble_box → hbox → ListBoxRow
            row = self._asst_lbl.get_parent()
            for _ in range(2):
                row = row.get_parent() if row else None
            if row and isinstance(row, Gtk.ListBoxRow):
                self._list_box.remove(row)
        self._asst_lbl = None
        self._status_lbl.set_text(msg)
        self._set_sending(False)
        return False

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _add_bubble(self, text: str, is_user: bool) -> Gtk.Label:
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0)
        )

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.set_margin_top(3)
        hbox.set_margin_bottom(3)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bubble.get_style_context().add_class(
            'minty-user' if is_user else 'minty-asst'
        )

        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_xalign(0)
        lbl.set_selectable(True)
        lbl.set_max_width_chars(40)
        lbl.get_style_context().add_class('minty-msg')
        bubble.add(lbl)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        if is_user:
            hbox.pack_start(spacer, True, True, 0)
            hbox.pack_start(bubble, False, False, 8)
        else:
            hbox.pack_start(bubble, False, False, 8)
            hbox.pack_start(spacer, True, True, 0)

        row.add(hbox)
        self._list_box.add(row)
        row.show_all()
        self._scroll_bottom()
        return lbl

    def _scroll_bottom(self):
        GLib.idle_add(self._do_scroll)

    def _do_scroll(self):
        adj = self._scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _set_sending(self, sending: bool):
        self._is_sending = sending
        self.entry.set_sensitive(not sending)
        self._send_btn.set_visible(not sending)
        if sending:
            self._cancel_btn.show()
        else:
            self._cancel_btn.hide()


# ══════════════════════════════════════════════════════════════════════════════
# Settings window
# ══════════════════════════════════════════════════════════════════════════════

class _SettingsWindow:
    """Model picker + system-prompt editor for MintyAI."""

    _instance = None

    @classmethod
    def open(cls, chat: _ChatWidget):
        if cls._instance and cls._instance._win.get_visible():
            cls._instance._win.present()
            return
        inst        = object.__new__(cls)
        inst._chat  = chat
        inst._build()
        cls._instance = inst

    def _build(self):
        win = Gtk.Window(title='Minty Settings')
        win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        win.set_resizable(False)
        win.set_keep_above(True)
        win.set_border_width(18)
        win.set_default_size(400, -1)
        win.set_accept_focus(True)
        win.connect('delete-event', lambda w, _e: w.hide() or True)
        self._win = win

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        win.add(vbox)

        # ── Model ─────────────────────────────────────────────────────────────
        vbox.pack_start(_bold_label('Model'), False, False, 0)

        self._model_combo = Gtk.ComboBoxText()
        self._model_combo.append_text(self._chat.current_model)
        self._model_combo.set_active(0)
        vbox.pack_start(self._model_combo, False, False, 0)

        threading.Thread(target=self._load_models, daemon=True).start()

        # ── System prompt ──────────────────────────────────────────────────────
        vbox.pack_start(_bold_label('System prompt'), False, False, 0)

        self._prompt_view = Gtk.TextView()
        self._prompt_view.set_editable(True)
        self._prompt_view.set_cursor_visible(True)
        self._prompt_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self._prompt_view.set_left_margin(8)
        self._prompt_view.set_right_margin(8)
        self._prompt_view.set_top_margin(6)
        self._prompt_view.set_bottom_margin(6)
        self._prompt_view.get_buffer().set_text(self._chat._system_prompt)

        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_shadow_type(Gtk.ShadowType.IN)
        prompt_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        prompt_scroll.set_size_request(-1, 150)
        prompt_scroll.add(self._prompt_view)
        vbox.pack_start(prompt_scroll, False, False, 0)

        hint = Gtk.Label(label='Changes take effect in new chats.')
        hint.set_halign(Gtk.Align.START)
        hint.get_style_context().add_class('cal-dim')
        vbox.pack_start(hint, False, False, 0)

        # ── Buttons ────────────────────────────────────────────────────────────
        vbox.pack_start(Gtk.Separator(), False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        panel_prefs_btn = Gtk.Button(label='Panel Preferences…')
        panel_prefs_btn.connect('clicked', self._on_panel_prefs)
        btn_row.pack_start(panel_prefs_btn, False, False, 0)

        # spacer
        btn_row.pack_start(Gtk.Box(), True, True, 0)

        apply_btn = Gtk.Button(label='Apply')
        apply_btn.connect('clicked', self._on_apply)
        btn_row.pack_start(apply_btn, False, False, 0)

        close_btn = Gtk.Button(label='Close')
        close_btn.connect('clicked', lambda _: self._win.hide())
        btn_row.pack_start(close_btn, False, False, 0)

        vbox.pack_start(btn_row, False, False, 0)
        win.show_all()

    def _on_panel_prefs(self, _btn):
        self._win.hide()
        from applets.appmenu import PreferencesWindow
        PreferencesWindow(self._chat._panel).present()

    def _on_apply(self, _btn):
        model = self._model_combo.get_active_text()
        if model:
            self._chat._model = model

        buf    = self._prompt_view.get_buffer()
        prompt = buf.get_text(
            buf.get_start_iter(), buf.get_end_iter(), False
        ).strip()
        if prompt:
            self._chat._system_prompt = prompt

        self._win.hide()

    # ── Model listing (background thread) ─────────────────────────────────────

    def _load_models(self):
        try:
            models = _fetch_models()
            if models:
                GLib.idle_add(self._populate_models, models)
        except Exception:
            pass

    def _populate_models(self, models: list):
        current = self._chat.current_model
        self._model_combo.remove_all()
        for name in models:
            self._model_combo.append_text(name)
        for i, name in enumerate(models):
            if name == current:
                self._model_combo.set_active(i)
                return
        for i, name in enumerate(models):
            if DEFAULT_MODEL in name:
                self._model_combo.set_active(i)
                return
        self._model_combo.set_active(0)
        return False


def _bold_label(text: str) -> Gtk.Label:
    lbl = Gtk.Label()
    lbl.set_markup(f'<b>{text}</b>')
    lbl.set_halign(Gtk.Align.START)
    return lbl

