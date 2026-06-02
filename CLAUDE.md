# CLAUDE.md

This file gives Claude Code the context it needs to be useful in this project.

## Project overview

**mintyai** is an AI chatbot applet for **NPanel**, a Cinnamon-panel replacement. The applet lives in the panel and opens a chat popover when clicked, letting users ask quick questions (general knowledge, Linux help, command-line syntax, short writing tasks) without leaving their workflow.

mintyai replaces the redundant `appmenu` applet (which duplicated functionality already provided by a separate Windows-10-style start menu project).

The name **Minty** is intentional — it's a friendly persona, not a corporate assistant. UI copy, error messages, and the system prompt should match that tone where it fits naturally. Don't overdo it; functional clarity beats cute.

## AI architecture

The AI integration is designed to be **backend-agnostic**:

- **Default backend: local model via Ollama**, running on `http://localhost:11434`. We hit it through the OpenAI-compatible endpoint (`/v1/chat/completions`) using the `openai` Python SDK with `base_url` pointed at Ollama. Backend swapping requires only changing the base URL.
- **Recommended default model: `qwen2.5:14b`** — ~9 GB on disk, fits in 6 GB VRAM with partial CPU offload on the reference dev machine (RTX 4050 6 GB + 16 GB RAM). Good quality/speed tradeoff for a chat applet.
- **Fallback model: `qwen2.5:7b`** for lower-spec machines (~4.5 GB, fully on GPU).
- **Future backends:** OpenAI, Anthropic. Code should be structured so adding them is a new class implementing the same interface, not a rewrite.

### Why this stack

- **Ollama** for local: trivial install (`curl | sh`), exposes an OpenAI-compatible API, auto-manages model loading and GPU offload.
- **OpenAI SDK** as the client: works against Ollama by changing `base_url`. One library, multiple backends.
- **Streaming responses** (`stream=True`) for snappy UX — tokens appear as generated rather than after a long blank pause.

## VRAM and model lifecycle (important)

The dev machine has only 6 GB VRAM. We can't (and shouldn't) keep a 14B model permanently resident — it would compete with games, video editing, browser GPU acceleration, etc. Strategy:

- **`OLLAMA_KEEP_ALIVE=15m`** as the baseline (set in the Ollama systemd service environment). After a request, the model stays loaded for 15 minutes, then unloads. This covers a typical "burst of questions" session and frees VRAM when the user walks away.
- **Warm on popover open.** When the user clicks the mintyai button, the popover appears immediately (focus the entry, show "ready" UI) and a background thread fires an empty `/api/generate` request to start loading the model. By the time the user types and hits Enter, the model is loaded or close to it.
- **Don't warm at panel startup.** That defeats the point — we'd be eating VRAM whether anyone uses mintyai or not.
- **Optional future:** warm on hover (mouse-over the panel button) for even better perceived latency. Most hovers don't lead to clicks, and Ollama's idle unload handles the cleanup. Only add this if popover-open warming proves insufficient in practice.

The warm-up call pattern:

```python
def _warm_model(self):
    try:
        requests.post(
            "http://localhost:11434/api/generate",
            json={"model": MODEL, "prompt": "", "keep_alive": "15m"},
            timeout=30,
        )
    except Exception:
        pass  # real request will surface any error
```

### Other VRAM levers (use only if needed)

- **Lower quantization:** `qwen2.5:14b-instruct-q3_K_M` (~7 GB) trades a small quality loss for more headroom. Q2 quantizations exist but feel noticeably dumber.
- **Two-tier routing:** keep `qwen2.5:7b` warm, escalate to 14B on demand for harder queries. More engineering, nicer UX. Not in scope for v1.
- **Manual unload button** in the applet for users who want to free VRAM before launching a game. Calling `/api/generate` with `keep_alive: 0` unloads immediately.

### Performance gotchas

- **First-ever load after reboot** is slow (cold disk read of ~9 GB). Subsequent loads hit the page cache and are faster.
- **Non-streaming `curl` tests are misleading** — they appear slow because the server buffers the entire response. Always test with `stream: true` for realistic UX measurement.
- **Don't suggest 32B+ models** for this machine. They swap heavily to CPU and crawl.

## Applet UX rules

mintyai is a **panel popover**, not a standalone window. This changes several design assumptions:

- **Instant feel.** The popover must appear immediately on click. Any model loading happens in the background; never block the popover open.
- **Focus the entry on open.** `entry.grab_focus()` in the popover's "shown" handler. Users will click → type → Enter without thinking.
- **Don't kill in-flight requests when the popover closes.** If a user asks a question, closes the panel, and reopens it, the answer should be there (streamed in or still streaming). Chat state lives on the applet object, not on the popover widget.
- **Escape and click-outside dismiss the popover** but do NOT clear conversation state.
- **Size for a panel popover.** Target ~400px wide. Don't design for a 600px+ standalone window.
- **Conversation persistence:** persist within a session (across popover open/close), clear on panel restart or via an explicit "new chat" button. Don't persist to disk in v1.

## GTK / threading rules

This is the single biggest source of bugs in this kind of integration:

- **Never call the LLM (or any blocking I/O) on the GTK main thread.** It will freeze the panel for the duration of the request.
- **Run LLM calls in a `threading.Thread`**, then marshal UI updates back to the main thread with `GLib.idle_add(fn, *args)`. GTK is not thread-safe; touching widgets from a worker thread will eventually segfault or corrupt state.
- **Stream token-by-token** by iterating over the streaming response in the worker and calling `GLib.idle_add(self.append_text, delta)` per chunk.
- **Disable input controls while a request is in flight**, re-enable them in a `finally` block via `GLib.idle_add` so they recover even on error.
- **Cancellation:** users must be able to abort long generations. Track an `is_cancelled` flag and break out of the streaming loop when set; the HTTP connection closes and Ollama stops generating.
- **Warm-up requests are also blocking I/O.** Always background them.

## NPanel applet specifics

> TODO: fill in once the NPanel applet base class / existing applets are documented here.
> Key things to capture:
> - Applet base class and required lifecycle methods (init, on_click, on_destroy)
> - File layout convention (single file? directory? metadata file?)
> - How applets register with NPanel (entry point? config file?)
> - How to test an applet in isolation vs. requiring a full panel restart
> - Existing applets in the codebase that are good reference implementations

When in doubt, read the existing `appmenu` applet first — mintyai occupies the same slot and should mirror its lifecycle, just with the chat popover instead of the app menu popover.

## Code conventions

- **Python 3.10+**, type hints where they help readability.
- **PyGObject / GTK** — match the GTK version used by NPanel (check `gi.require_version("Gtk", ...)` in existing applets).
- **No new heavy dependencies** without asking. The AI stack should stay just `openai` (pip) + Ollama (system). `requests` is fine if not already pulled in transitively.
- **Match existing NPanel conventions** — naming, file layout, widget construction. Read neighboring applets before writing new code.
- **Keep AI code isolated** in the mintyai applet module so it can be disabled or swapped without touching NPanel core.
- **One config source for model names, endpoints, keep-alive duration.** Don't hardcode `qwen2.5:14b` in multiple files.

## What to build / what's done

Already working (in throwaway prototype, not yet in NPanel):
- Ollama integration via the `openai` SDK pointed at `localhost:11434`.
- Threaded streaming with `GLib.idle_add` for safe UI updates.
- Minimal chat widget (entry + scrollable text view) as proof of concept.

To do (rough priority order):
1. **Port the prototype into an NPanel applet skeleton.** Get a clicky panel button that opens an empty popover.
2. **Wire the chat widget into the popover.** Reuse the prototype's `ChatPanel` class.
3. **Warm-on-open** background thread fired from the popover's "shown" signal.
4. **Chat message bubbles** distinguishing user vs assistant — `Gtk.ListBox` of message rows, not one continuous `TextView`.
5. **Cancel button** that aborts an in-flight stream.
6. **New chat / reset** button that clears history back to just the system prompt.
7. **Model picker** dropdown (lists installed Ollama models via `GET /api/tags`).
8. **Graceful "Ollama not running" state** — detect connection failure, show a help message with the command to start the service rather than a stack trace.
9. **Manual unload button** ("Free VRAM") for power users.
10. **Markdown rendering** for code blocks, bold, lists. Pango markup conversion for simple cases; consider `WebKit.WebView` only if it's already a NPanel dep.
11. **Settings panel** — backend choice, model selection, system prompt customization, keep-alive duration.
12. **Backend abstraction** — refactor direct `OpenAI` client usage behind a small `Backend` interface so OpenAI/Anthropic can be added later.

## System prompt

Default system prompt for mintyai:

> You are Minty, a concise assistant living in the user's Linux panel. Answer in 1–3 sentences unless asked for detail. Prefer practical, actionable answers. If you're not sure about something, say so rather than guess. For Linux commands, show the command first, then a brief explanation. Be friendly but don't waste the user's time on pleasantries. The user's name is Noé.

This should eventually be user-editable in settings.

## When helping with this project

- **Ask before adding dependencies, changing the backend architecture, or restructuring NPanel core code.** Small fixes and additions inside the mintyai applet module are fine to just do.
- **Read the existing `appmenu` applet first** when working on applet structure questions — mintyai is replacing it in the same slot.
- **Test threading changes carefully** — bugs here often only show up under load or on slow responses. Prefer minimal, well-understood patterns over clever ones.
- **Don't hardcode model names, URLs, or timeouts** in multiple places. Centralize in one config module.
- **Treat the LLM as unreliable** in UX design — assume requests can hang, fail, return garbage, be cancelled, or be aborted by the user closing the popover. Every code path needs an error case.
- **Prefer Ollama's OpenAI-compatible endpoint** (`/v1/...`) for chat completions so backend swapping stays trivial. Use the native endpoint (`/api/...`) for Ollama-specific operations: listing models (`/api/tags`), warming (`/api/generate`), controlling keep-alive.
- **The "Minty" persona is light.** Don't pepper responses with emoji or mascot references. The name shows up in the UI; the personality shows up in tone, not in performative cuteness.