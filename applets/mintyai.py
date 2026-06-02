"""applets/mintyai.py — Minty AI chat applet for PillPanel."""

import html as _html
import json
import logging
import re
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
DEFAULT_MODEL   = 'qwen3:14b'
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
button.minty-think-btn {
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
button.minty-think-btn:hover {
    background: rgba(255,255,255,0.07);
    color: rgba(255,255,255,0.85);
}
button.minty-think-btn:checked {
    background: rgba(135, 192, 80, 0.15);
    border-color: rgba(135, 192, 80, 0.50);
    color: #87c050;
}
button.minty-think-btn:checked:hover {
    background: rgba(135, 192, 80, 0.22);
}
"""

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


def _fetch_models_full() -> list:
    """Return list of dicts with 'name' and 'size' keys."""
    req = urllib.request.Request(f'{OLLAMA_API_BASE}/tags')
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return data.get('models', [])


def _human_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


# ── WebKit2 (optional — markdown rendering) ───────────────────────────────────

_WebKit2 = None
for _wk_ver in ('4.1', '4.0'):
    try:
        gi.require_version('WebKit2', _wk_ver)
        from gi.repository import WebKit2 as _WebKit2
        break
    except Exception:
        pass
_HAS_WEBKIT = _WebKit2 is not None

# ── Markdown → HTML ────────────────────────────────────────────────────────────

def _has_md(text: str) -> bool:
    return bool(re.search(r'```|`[^`]|\*\*|^#{1,3} |^[-*+] |^\d+[.)]\s', text, re.M))


def _inline(text: str) -> str:
    """Inline markdown on already-HTML-escaped text: inline code, bold, italic."""
    parts  = re.split(r'`([^`]+)`', text)
    result = []
    for i, p in enumerate(parts):
        if i % 2:
            result.append(f'<code>{_html.escape(p)}</code>')
        else:
            s = _html.escape(p)
            s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
            s = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',          s)
            result.append(s)
    return ''.join(result)


def _md_to_html(text: str) -> str:
    """Convert LLM markdown to safe HTML (no external deps)."""
    blocks = []

    def pull_block(m):
        lang = m.group(1).strip()
        code = _html.escape(m.group(2).rstrip('\n'))
        blocks.append((lang, code))
        return f'\x00B{len(blocks)-1}\x00'

    text = re.sub(r'```([^\n`]*)\n?(.*?)```', pull_block, text, flags=re.DOTALL)

    out, in_ul, in_ol = [], False, False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul: out.append('</ul>'); in_ul = False
        if in_ol: out.append('</ol>'); in_ol = False

    for line in text.split('\n'):
        if '\x00B' in line:
            close_lists(); out.append(line); continue
        if m := re.match(r'^(#{1,3}) (.+)', line):
            close_lists()
            n = len(m.group(1))
            out.append(f'<h{n}>{_inline(m.group(2))}</h{n}>'); continue
        if m := re.match(r'^[ \t]*[-*+] (.+)', line):
            if in_ol: out.append('</ol>'); in_ol = False
            if not in_ul: out.append('<ul>'); in_ul = True
            out.append(f'<li>{_inline(m.group(1))}</li>'); continue
        if m := re.match(r'^[ \t]*\d+[.)]\s+(.+)', line):
            if in_ul: out.append('</ul>'); in_ul = False
            if not in_ol: out.append('<ol>'); in_ol = True
            out.append(f'<li>{_inline(m.group(1))}</li>'); continue
        close_lists()
        s = line.strip()
        if not s:
            out.append('<br>'); continue
        out.append(f'<p>{_inline(s)}</p>')

    close_lists()
    result = '\n'.join(out)

    for i, (lang, code) in enumerate(blocks):
        lc = f' class="lang-{lang}"' if lang else ''
        result = result.replace(
            f'\x00B{i}\x00',
            f'<div class="cb">'
            f'<button class="cp" onclick="cpBlock(this)">Copy</button>'
            f'<pre><code{lc}>{code}</code></pre>'
            f'</div>',
        )
    return result


_WK_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: transparent;
    color: rgba(255,255,255,0.92);
    font-family: sans-serif;
    font-size: 13px;
    line-height: 1.4;
    padding: 2px 8px 4px 8px;
    overflow: hidden;
    word-break: break-word;
}
p { margin: 0 0 5px; }
p:last-child { margin-bottom: 0; }
h1, h2, h3 { font-size: 14px; font-weight: 600; margin: 6px 0 3px; }
h1 { font-size: 15px; }
ul, ol { padding-left: 18px; margin: 3px 0 5px; }
li { margin: 1px 0; }
br { display: block; content: ''; margin-top: 3px; }
.cb { position: relative; margin: 6px 0; }
pre {
    background: rgba(0,0,0,0.35);
    border-radius: 7px;
    padding: 10px 12px;
    overflow-x: auto;
    white-space: pre;
    font-size: 12px;
    line-height: 1.45;
}
code { font-family: 'Cascadia Code','JetBrains Mono','Fira Code',monospace; }
p > code, li > code {
    background: rgba(0,0,0,0.28);
    border-radius: 3px;
    padding: 1px 5px;
}
.cp {
    position: absolute;
    top: 7px; right: 7px;
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 4px;
    color: rgba(255,255,255,0.60);
    cursor: pointer;
    font: 11px/1 inherit;
    padding: 3px 8px;
}
.cp:hover { background: rgba(255,255,255,0.20); color: white; }
.cp.ok    { color: #87c050; border-color: #87c050; }
::-webkit-scrollbar { height: 4px; width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.22); border-radius: 2px; }
details.thinking {
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 6px;
    margin-bottom: 8px;
    overflow: hidden;
}
details.thinking > summary {
    cursor: pointer;
    padding: 5px 10px;
    color: rgba(255,255,255,0.42);
    font-size: 11px;
    user-select: none;
    list-style: none;
    outline: none;
}
details.thinking > summary::-webkit-details-marker { display: none; }
details.thinking > summary::before { content: '▶  '; font-size: 9px; }
details.thinking[open] > summary::before { content: '▼  '; }
details.thinking > summary:hover { color: rgba(255,255,255,0.65); background: rgba(255,255,255,0.04); }
.think-content {
    padding: 8px 12px;
    border-top: 1px solid rgba(255,255,255,0.07);
    color: rgba(255,255,255,0.38);
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.4;
    max-height: 220px;
    overflow-y: auto;
}
"""

_WK_JS = """
function cpBlock(btn) {
    var code = btn.nextElementSibling.querySelector('code') || btn.nextElementSibling;
    var ta   = document.createElement('textarea');
    ta.value = code.textContent;
    ta.style.cssText = 'position:fixed;top:-9999px;left:0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(e) {}
    document.body.removeChild(ta);
    btn.textContent = '✓ Copied'; btn.classList.add('ok');
    setTimeout(function(){ btn.textContent='Copy'; btn.classList.remove('ok'); }, 2000);
}
"""


def _wk_page(content: str) -> str:
    return (f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
            f'<style>{_WK_CSS}</style></head>'
            f'<body>{content}'
            f'<script>{_WK_JS}</script></body></html>')


# ── Assistant bubble ───────────────────────────────────────────────────────────

class _AsstBubble:
    """
    Streams text into a Gtk.Label; upgrades to a WebKit2.WebView on finalize()
    when the response contains markdown worth rendering.
    """

    def __init__(self, popup_width: int = POPUP_WIDTH, scroll_fn=None):
        self._popup_width = popup_width
        self._scroll_fn   = scroll_fn
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.widget.set_margin_start(8)
        self.widget.set_margin_end(8)
        self.widget.set_margin_top(2)
        self.widget.set_margin_bottom(2)

        self._text = ''
        self._lbl  = Gtk.Label()
        self._lbl.set_line_wrap(True)
        self._lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._lbl.set_xalign(0)
        self._lbl.set_selectable(True)
        self._lbl.set_max_width_chars(42)
        self._lbl.get_style_context().add_class('minty-msg')
        self.widget.add(self._lbl)
        self.widget.show_all()

    def append(self, token: str):
        self._text += token
        self._lbl.set_text(self._text)

    def finalize(self, thinking: str = ''):
        if _HAS_WEBKIT and (thinking or _has_md(self._text)):
            self._upgrade(thinking)

    def _upgrade(self, thinking: str = ''):
        self.widget.remove(self._lbl)
        wv = _WebKit2.WebView()
        s  = wv.get_settings()
        s.set_enable_javascript(True)
        s.set_default_font_size(13)
        s.set_default_monospace_font_size(12)
        gtk_font = Gtk.Settings.get_default().get_property('gtk-font-name') or ''
        family   = gtk_font.rsplit(' ', 1)[0] if gtk_font else 'sans-serif'
        s.set_default_font_family(family)
        s.set_sans_serif_font_family(family)
        wv.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        wv.connect('context-menu', lambda *_: True)
        wv.connect('load-changed', self._on_load)
        est = 300 if ('```' in self._text or thinking) else max(60, self._text.count('\n') * 22)
        wv.set_size_request(self._popup_width - 16, est)
        # Build page: optional collapsible thinking block + rendered content
        parts = []
        if thinking:
            esc = _html.escape(thinking)
            parts.append(
                f'<details class="thinking" open>'
                f'<summary>Thinking</summary>'
                f'<div class="think-content">{esc}</div>'
                f'</details>'
            )
        parts.append(_md_to_html(self._text) if self._text else '')
        wv.load_html(_wk_page(''.join(parts)), None)
        self._wv = wv
        self.widget.add(wv)
        wv.show()

    def _on_load(self, wv, event):
        if event == _WebKit2.LoadEvent.FINISHED:
            # Small delay so CSS layout settles before we measure
            GLib.timeout_add(80, self._query_height, wv)

    def _query_height(self, wv):
        wv.run_javascript(
            'Math.max(document.body.scrollHeight,'
            '         document.documentElement.scrollHeight)',
            None, self._on_height, None,
        )
        return False  # don't repeat

    def _on_height(self, source, result, _):
        try:
            h = source.run_javascript_finish(result).get_js_value().to_int32()
            GLib.idle_add(self._apply_height, source, h + 4)
        except Exception as exc:
            log.debug(f'[Minty] WebView height: {exc}')

    def _apply_height(self, wv, h):
        wv.set_size_request(self._popup_width - 16, h)
        w = wv
        while w:
            w = w.get_parent()
            if isinstance(w, Gtk.ListBox):
                w.queue_resize()
                break
        # Scroll after layout reflow settles (idle_add is too soon after queue_resize)
        if self._scroll_fn:
            fn = self._scroll_fn
            GLib.timeout_add(300, lambda: fn() or False)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Applet
# ══════════════════════════════════════════════════════════════════════════════

class MintyAIApplet(Applet):
    """Minty AI — streaming chat via Ollama, shown in a panel popup."""

    def build(self):
        _install_css()
        self._chat  = _ChatWidget(self.panel,
                                  popup_visible_fn=lambda: self._popup._visible)
        self._popup = PanelPopup(self._chat.root)

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
        except Exception:
            pass

    def destroy(self):
        self._chat.cancel()


# ══════════════════════════════════════════════════════════════════════════════
# Chat widget
# ══════════════════════════════════════════════════════════════════════════════

class _ChatWidget:
    """Self-contained chat UI. Instantiated once; lives on the applet."""

    def __init__(self, panel, popup_visible_fn=None):
        self._panel             = panel
        self._popup_visible_fn  = popup_visible_fn or (lambda: True)
        self._model             = panel.config.get('minty_model', DEFAULT_MODEL)
        self._system_prompt     = SYSTEM_PROMPT
        self._history           = [{'role': 'system', 'content': self._system_prompt}]
        self._popup_width       = panel.config.get('minty_popup_width', POPUP_WIDTH)
        self._chat_height       = panel.config.get('minty_chat_height', CHAT_HEIGHT)
        self._is_sending        = False
        self._is_cancelled      = False
        self._asst_bubble       = None
        self._asst_text         = ''
        self._asst_thinking     = ''   # accumulated thinking tokens
        self.root               = self._build()

    # ── Public ────────────────────────────────────────────────────────────────

    @property
    def current_model(self) -> str:
        return self._model

    def cancel(self):
        self._is_cancelled = True

    def new_chat(self):
        self.cancel()
        self._history       = [{'role': 'system', 'content': self._system_prompt}]
        self._asst_thinking = ''
        for row in self._list_box.get_children():
            self._list_box.remove(row)
        self._status_lbl.set_text('')
        self._set_sending(False)

    def resize(self, width: int, height: int):
        self._popup_width = width
        self._chat_height = height
        self.root.set_size_request(width, -1)
        self._scroll.set_size_request(-1, height)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(self._popup_width, -1)

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

        self._think_btn = Gtk.ToggleButton(label='Think')
        self._think_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._think_btn.get_style_context().add_class('minty-think-btn')
        self._think_btn.set_active(False)
        self._think_btn.set_tooltip_text('Enable thinking mode (qwen3)')
        hdr.pack_start(self._think_btn, False, False, 0)

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
        self._scroll.set_size_request(-1, self._chat_height)
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
        self._asst_text     = ''
        self._asst_thinking = ''
        self._asst_bubble   = self._add_bubble('', is_user=False)
        self._is_cancelled = False
        self._set_sending(True)
        self._status_lbl.set_text('Generating…')
        threading.Thread(
            target=self._stream_thread,
            args=(list(self._history), self.current_model,
                  self._think_btn.get_active()),
            daemon=True,
        ).start()

    def _stream_thread(self, messages: list, model: str, think: bool):
        """Stream from Ollama's native /api/chat endpoint.

        The native API returns thinking and content as separate fields,
        so we get clean separation without any tag-parsing hacks.
        """
        try:
            data = json.dumps({
                'model':    model,
                'messages': messages,
                'stream':   True,
                'think':    think,
            }).encode()
            req = urllib.request.Request(
                f'{OLLAMA_API_BASE}/chat',
                data=data,
                headers={'Content-Type': 'application/json'},
            )
            _thinking_started = False
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw in resp:
                    if self._is_cancelled:
                        break
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get('message', {})

                    # thinking field → accumulate for collapsible display later
                    thinking_delta = msg.get('thinking') or ''
                    if thinking_delta:
                        if not _thinking_started:
                            _thinking_started = True
                            GLib.idle_add(self._status_lbl.set_text, 'Thinking…')
                        GLib.idle_add(self._on_thinking_token, thinking_delta)

                    # content field → actual response to show
                    content = msg.get('content') or ''
                    if content:
                        if _thinking_started:
                            _thinking_started = False
                            GLib.idle_add(self._status_lbl.set_text, '')
                        GLib.idle_add(self._on_token, content)

                    if chunk.get('done'):
                        break

            GLib.idle_add(self._on_done)

        except Exception as exc:
            msg = str(exc)
            if 'Connection refused' in msg or 'connect' in msg.lower():
                msg = 'Ollama is not running.\nStart it with: ollama serve'
            GLib.idle_add(self._on_error, msg)

    # ── GTK-thread callbacks ───────────────────────────────────────────────────

    def _on_thinking_token(self, token: str):
        self._asst_thinking += token
        return False

    def _on_token(self, token: str):
        self._asst_text += token
        if self._asst_bubble:
            self._asst_bubble.append(token)
        self._scroll_bottom()
        return False

    def _on_done(self):
        if self._asst_text:
            self._history.append(
                {'role': 'assistant', 'content': self._asst_text}
            )
        if self._asst_bubble:
            self._asst_bubble.finalize(self._asst_thinking)
        self._status_lbl.set_text('')
        self._set_sending(False)
        self._asst_bubble = None
        self._scroll_bottom()
        # Notify if the popup is closed
        if not self._popup_visible_fn():
            self._notify_done()
        return False

    def _notify_done(self):
        import subprocess
        preview = self._asst_text[:100].strip().replace('\n', ' ')
        if len(self._asst_text) > 100:
            preview += '…'
        try:
            subprocess.Popen([
                'notify-send',
                '--icon=dialog-information',
                '--expire-time=6000',
                'Minty',
                preview or 'Response ready.',
            ])
        except Exception:
            pass

    def _on_error(self, msg: str):
        self._asst_thinking = ''
        if not self._asst_text and self._asst_bubble:
            row = self._asst_bubble.widget.get_parent()
            if row:
                row = row.get_parent()
            if row and isinstance(row, Gtk.ListBoxRow):
                self._list_box.remove(row)
        self._asst_bubble = None
        self._status_lbl.set_text(msg)
        self._set_sending(False)
        return False

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _add_bubble(self, text: str, is_user: bool):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0)
        )

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.set_margin_top(3)
        hbox.set_margin_bottom(3)

        if is_user:
            bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bubble.get_style_context().add_class('minty-user')
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
            hbox.pack_start(spacer, True, True, 0)
            hbox.pack_start(bubble, False, False, 8)
            row.add(hbox)
            self._list_box.add(row)
            row.show_all()
            self._scroll_bottom()
            return None

        else:
            asst = _AsstBubble(self._popup_width, scroll_fn=self._scroll_bottom)
            hbox.pack_start(asst.widget, True, True, 0)
            row.add(hbox)
            self._list_box.add(row)
            row.show_all()
            self._scroll_bottom()
            return asst

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

        # ── Dimensions ─────────────────────────────────────────────────────────
        vbox.pack_start(_bold_label('Dimensions'), False, False, 0)

        dim_grid = Gtk.Grid()
        dim_grid.set_column_spacing(12)
        dim_grid.set_row_spacing(6)

        w_lbl = Gtk.Label(label='Popup width (px)')
        w_lbl.set_halign(Gtk.Align.START)
        dim_grid.attach(w_lbl, 0, 0, 1, 1)
        self._width_spin = Gtk.SpinButton.new_with_range(300, 700, 20)
        self._width_spin.set_value(self._chat._popup_width)
        dim_grid.attach(self._width_spin, 1, 0, 1, 1)

        h_lbl = Gtk.Label(label='Chat height (px)')
        h_lbl.set_halign(Gtk.Align.START)
        dim_grid.attach(h_lbl, 0, 1, 1, 1)
        self._height_spin = Gtk.SpinButton.new_with_range(200, 900, 20)
        self._height_spin.set_value(self._chat._chat_height)
        dim_grid.attach(self._height_spin, 1, 1, 1, 1)

        vbox.pack_start(dim_grid, False, False, 0)

        # ── Installed models ───────────────────────────────────────────────────
        vbox.pack_start(_bold_label('Installed models'), False, False, 0)

        self._models_lb = Gtk.ListBox()
        self._models_lb.set_selection_mode(Gtk.SelectionMode.NONE)

        models_scroll = Gtk.ScrolledWindow()
        models_scroll.set_shadow_type(Gtk.ShadowType.IN)
        models_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        models_scroll.set_min_content_height(40)
        models_scroll.set_max_content_height(180)
        models_scroll.add(self._models_lb)
        vbox.pack_start(models_scroll, False, False, 0)

        # Load combo + list together now that both widgets exist
        threading.Thread(target=self._load_models_full, daemon=True).start()

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
            self._chat._panel.config['minty_model'] = model

        buf    = self._prompt_view.get_buffer()
        prompt = buf.get_text(
            buf.get_start_iter(), buf.get_end_iter(), False
        ).strip()
        if prompt:
            self._chat._system_prompt = prompt

        w = int(self._width_spin.get_value())
        h = int(self._height_spin.get_value())
        self._chat._panel.config['minty_popup_width'] = w
        self._chat._panel.config['minty_chat_height'] = h
        from applets.appmenu import _save as _save_cfg
        _save_cfg(self._chat._panel.config)
        self._chat.resize(w, h)

        self._win.hide()

    # ── Model loading (background thread) ─────────────────────────────────────

    def _load_models_full(self):
        try:
            models = _fetch_models_full()
        except Exception:
            models = []
        GLib.idle_add(self._populate_all, models)

    def _populate_all(self, models: list):
        current = self._chat.current_model
        names   = [m['name'] for m in models]

        # Combo
        self._model_combo.remove_all()
        for name in names:
            self._model_combo.append_text(name)
        for i, name in enumerate(names):
            if name == current:
                self._model_combo.set_active(i); break
        else:
            for i, name in enumerate(names):
                if DEFAULT_MODEL in name:
                    self._model_combo.set_active(i); break
            else:
                if names: self._model_combo.set_active(0)

        # Installed-models list
        for row in self._models_lb.get_children():
            self._models_lb.remove(row)

        if not models:
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            lbl = Gtk.Label(label='No models found — is Ollama running?')
            lbl.set_margin_start(8); lbl.set_margin_top(6); lbl.set_margin_bottom(6)
            lbl.get_style_context().add_class('cal-dim')
            row.add(lbl)
            self._models_lb.add(row)
        else:
            for m in models:
                self._add_model_row(m['name'], m.get('size', 0))

        self._models_lb.show_all()
        return False

    def _add_model_row(self, name: str, size_bytes: int):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8); box.set_margin_end(8)
        box.set_margin_top(5);   box.set_margin_bottom(5)

        name_lbl = Gtk.Label(label=name)
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.set_hexpand(True)
        box.pack_start(name_lbl, True, True, 0)

        if size_bytes:
            size_lbl = Gtk.Label(label=_human_size(size_bytes))
            size_lbl.get_style_context().add_class('cal-dim')
            box.pack_start(size_lbl, False, False, 0)

        del_btn = Gtk.Button(label='Delete')
        del_btn.connect('clicked', lambda _b, n=name, r=row: self._confirm_delete(n, r))
        box.pack_start(del_btn, False, False, 0)

        row.add(box)
        self._models_lb.add(row)

    # ── Model deletion ─────────────────────────────────────────────────────────

    def _confirm_delete(self, name: str, row: Gtk.ListBoxRow):
        dlg = Gtk.MessageDialog(
            transient_for=self._win,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Delete "{name}"?',
        )
        dlg.format_secondary_text(
            'This removes the model from disk.\n'
            f'Re-download later with: ollama pull {name}'
        )
        response = dlg.run()
        dlg.destroy()
        if response == Gtk.ResponseType.YES:
            row.set_sensitive(False)
            threading.Thread(
                target=self._delete_thread, args=(name, row), daemon=True
            ).start()

    def _delete_thread(self, name: str, row: Gtk.ListBoxRow):
        try:
            data = json.dumps({'name': name}).encode()
            req  = urllib.request.Request(
                f'{OLLAMA_API_BASE}/delete',
                data=data,
                headers={'Content-Type': 'application/json'},
                method='DELETE',
            )
            urllib.request.urlopen(req, timeout=30)
            GLib.idle_add(self._after_delete, row, None)
        except Exception as exc:
            GLib.idle_add(self._after_delete, row, str(exc))

    def _after_delete(self, row: Gtk.ListBoxRow, error):
        if error:
            row.set_sensitive(True)
            dlg = Gtk.MessageDialog(
                transient_for=self._win, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text='Delete failed',
            )
            dlg.format_secondary_text(error[:300])
            dlg.run(); dlg.destroy()
        else:
            self._models_lb.remove(row)
            # Refresh combo in case the deleted model was selected
            threading.Thread(target=self._load_models_full, daemon=True).start()
        return False


def _bold_label(text: str) -> Gtk.Label:
    lbl = Gtk.Label()
    lbl.set_markup(f'<b>{text}</b>')
    lbl.set_halign(Gtk.Align.START)
    return lbl

