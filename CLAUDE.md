# CLAUDE.md — TrayProbe (technical risk-reducer for custom Cinnamon panel)

## Project goal

Build a **minimal standalone Linux app whose only purpose is to display system tray
icons in its own window**. This is NOT the final panel. It is a focused prototype
whose sole job is to answer one question:

> Can a custom-written Python+GTK app successfully act as a StatusNotifierItem (SNI)
> tray host on this Linux Mint Cinnamon system, displaying real-world tray icons
> (network manager, volume, Bluetooth, ClickUp, Discord, etc.) when Cinnamon's own
> tray host has been disabled?

If the answer is yes, we proceed to build the full custom panel (PillPanel) with
confidence the hardest component works. If the answer is no, we have learned that
cheaply and can decide whether to push through with deeper debugging or change
direction.

**This is a technical spike. Optimise for clarity and verifiability, not polish.**

## Hard scope boundaries

IN scope for TrayProbe:
- One always-on-top window, fixed size, fixed position (top-center of screen is fine).
- Inside it: a horizontal row that displays whatever tray icons are currently registered
  via SNI / AppIndicator.
- Click an icon → fire its "Activate" action (left-click semantics).
- Right-click an icon → show its context menu (this is the part most likely to be hard;
  see "Known hard sub-problems" below).
- Icons appear and disappear live as apps register/unregister.
- Print verbose status to stdout: every SNI registration, every icon update, every error.
  We want logs we can paste into Claude when something goes wrong.

OUT of scope (do not build, will distract from the test):
- Panel layout, multiple sections, clock, menu, autohide.
- Pretty styling — basic dark background and rounded corners are fine, no theming.
- Configuration files, settings UI.
- XEmbed legacy tray support (we only care about SNI / AppIndicator; XEmbed is dying
  and not what blocks modern apps).
- Multiple-monitor handling.
- Wayland support (Cinnamon is X11).

## Target environment

- OS: Linux Mint 22 (Cinnamon, Ubuntu 24.04 / "noble" base).
- Display server: X11.
- Python: system python3 (3.12.x).
- GUI toolkit: GTK 3 via PyGObject (same as StartMenu).
- D-Bus library: **pydbus** (recommended — cleanest API for our needs) OR **dbus-next**
  (async, modern). Install whichever the assistant finds is currently maintained and works
  with our use case. Avoid the legacy `dbus-python` if a better option is available, but
  fall back to it if needed.
- Test apps: the user has ClickUp, network manager applet, volume control, Bluetooth
  manager, and similar already on the system — these are what should populate the tray.

## Critical: the SNI protocol in one paragraph

Tray icons in modern Linux use the **StatusNotifierItem (SNI)** D-Bus specification.
There are three D-Bus actors:
1. **StatusNotifierItem (SNI):** the application that wants a tray icon (e.g. ClickUp).
   It exposes itself as a D-Bus object on the session bus.
2. **StatusNotifierWatcher:** a singleton broker at the well-known bus name
   `org.kde.StatusNotifierWatcher`. SNIs register with the Watcher; Hosts ask the Watcher
   for the list of registered SNIs.
3. **StatusNotifierHost:** the panel / tray display. It registers with the Watcher under
   a name like `org.kde.StatusNotifierHost-<pid>`. Once registered, the Watcher tells it
   about all existing items, and signals it on new/removed items.

**Our app must implement both the Watcher AND the Host**, because there is no guarantee
a Watcher already exists when Cinnamon's panel is disabled. The reference Haskell
project (`taffybar` / `status-notifier-item`) provides a standalone watcher binary for
exactly this reason. We're building both into one Python process.

The XML interfaces (introspection definitions) for SNI, the Watcher, and DBusMenu (for
context menus) are documented at https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/
— the assistant should fetch these and use them as the authoritative source rather than
guessing method signatures.

## Architecture

Single-file Python script `trayprobe.py`. Structure:

1. **D-Bus service registration:** acquire ownership of the well-known name
   `org.kde.StatusNotifierWatcher` on the session bus. Fail loudly if another process
   already owns it (this is the "Cinnamon panel is still claiming the tray" diagnostic).
2. **Watcher implementation:** a D-Bus object at `/StatusNotifierWatcher` implementing
   the `org.kde.StatusNotifierWatcher` interface: `RegisterStatusNotifierItem`,
   `RegisterStatusNotifierHost`, properties (`RegisteredStatusNotifierItems`,
   `IsStatusNotifierHostRegistered`, `ProtocolVersion`), signals
   (`StatusNotifierItemRegistered`, `StatusNotifierItemUnregistered`,
   `StatusNotifierHostRegistered`).
3. **Host implementation:** register ourselves as a Host (`org.kde.StatusNotifierHost-<pid>`),
   query the Watcher's `RegisteredStatusNotifierItems`, then for each item connect to its
   `org.kde.StatusNotifierItem` D-Bus object and read its properties (Icon, Tooltip, Title,
   Menu path, Status).
4. **UI:** a GTK window with a horizontal Gtk.Box. For each registered item, add a
   Gtk.Button containing a Gtk.Image showing the icon. The icon comes from either:
   - `IconName` property (a themed icon name, render via `Gtk.Image.new_from_icon_name`).
   - `IconPixmap` property (raw pixel data; assemble into a GdkPixbuf).
   Most well-behaved apps provide `IconName`. Handle both.
5. **Interaction:**
   - Left-click on an item's button: D-Bus call `Activate(x, y)` on the item.
   - Right-click on an item's button: open its context menu. This is via the **DBusMenu**
     spec (`com.canonical.dbusmenu`), exposed at the path given by the item's `Menu` property.
     Rendering a DBusMenu as a GtkMenu is a project of its own — for v1, it's acceptable
     to just call the item's `ContextMenu(x, y)` method and let the application handle
     displaying the menu (some do, some don't). If most apps fail to show menus this way,
     v2 work would implement a DBusMenu→GtkMenu translator. **Document this clearly in
     the code with a TODO and don't get stuck trying to make it perfect.**

## Window structure (carry-over from PillPanel architecture)

Even though TrayProbe is just a spike, build the window with the **two-layer structure**
that the eventual PillPanel will use. This costs almost nothing now and means the layout
is proven by the time we write the real panel.

The structure:

- **Outer container** = the full-width transparent **hover/click detection area** that
  spans the top of the screen (or wherever the panel sits). This is what determines where
  mouse events register. It is intentionally larger than the visible UI so that the cursor
  reaching the screen edge lands inside the panel's reactive zone (this fixes the
  "cursor in the margin gap" flicker problem that Cinnamon's native panel suffers from
  when given a top margin).
- **Inner container** = the visible **pill**, smaller than the outer, centred within it,
  with rounded corners, dark background, and some margin between its edge and the outer
  container's edge. The tray icons and other content live inside this inner pill.

In GTK 3 terms: outer `Gtk.Window` (transparent background, sized to span screen width,
positioned at top) containing a `Gtk.Box` that is the visible pill (with CSS background,
border-radius, margin from window edges to create the float gap).

The transparent outer area only needs to extend on the side(s) where the user will
approach with the cursor — for a top panel, the relevant strip is above and at the top of
the pill, but covering all four sides is simpler and harmless.

For TrayProbe, the inner pill just needs to look "good enough" to confirm the structure:
dark background (e.g. `rgba(28, 28, 40, 0.92)`), border-radius around 18px, a small margin
(e.g. 6px on top, 8px on the sides) between the pill and the outer window edge. No fancy
styling beyond that. The point is to verify:
- The outer hover area really does register mouse events in the transparent margin.
- The inner pill renders correctly with rounded corners on an X11 compositor.
- Tray icons inside the pill behave correctly (don't overflow the rounded edges).

If any of those visual/structural points fails on this system (e.g. compositor doesn't
honour transparency, or click events leak through the transparent area to the desktop
below instead of being captured by our window), that's an *additional* finding worth
knowing before committing to PillPanel.

## Workflow for testing

1. **Identify what's currently claiming the tray.** Before running TrayProbe, find out:
   ```
   dbus-send --session --print-reply --dest=org.freedesktop.DBus \
     /org/freedesktop/DBus org.freedesktop.DBus.NameHasOwner \
     string:org.kde.StatusNotifierWatcher
   ```
   This tells us whether anyone owns the Watcher name. Cinnamon's panel typically does.
2. **Disable Cinnamon's panel** (temporarily — fully reversible):
   - The user will do this manually via: right-click panel → Panel Settings → ... or via
     killing the relevant Cinnamon process. The exact mechanism may need experimentation;
     document the user-facing rollback steps in the README section below.
   - WARNING: Disabling the Cinnamon panel removes the user's only access to settings,
     menu, etc. for the duration of the test. They must know how to roll back from a
     terminal. Make the rollback command part of the script's startup banner.
3. **Run TrayProbe:** `python3 trayprobe.py`. Window appears. Watch the stdout logs.
4. **Observe:**
   - Does the Watcher register successfully (no name conflict)?
   - Do existing app tray icons appear in the window?
   - Do left-clicks work (does volume opener show? does ClickUp respond?)?
   - Do right-clicks at least call ContextMenu (visible in logs even if menu doesn't render)?
   - Do icons appear/disappear live when an app starts/stops?
5. **The test result determines the next step.** If the icons populate, the project is
   unblocked. If not, the logs tell us where it failed.

## Known hard sub-problems (heads-up for the assistant)

- **DBusMenu rendering.** As noted above, translating DBusMenu D-Bus data to a working
  GtkMenu is genuinely complex (nested submenus, icons, separators, checkboxes, dynamic
  updates). For TrayProbe, attempt only the simplest path: call the item's `ContextMenu`
  D-Bus method and hope the app shows its own menu. If that doesn't work for most apps,
  log it and proceed; do not build a full DBusMenu renderer for the spike.
- **IconPixmap parsing.** SNI `IconPixmap` is an array of `(int width, int height, byte[] data)`
  tuples in ARGB32 network byte order. Pick the largest one that fits the panel, byte-swap
  if needed (it's big-endian over D-Bus), wrap in a GdkPixbuf. Apps that provide IconName
  instead avoid this hassle — most modern apps do.
- **Singleton Watcher conflicts.** If another Watcher is running (Cinnamon, an old TrayProbe
  instance, etc.), `RequestName` will fail. Detect this clearly and tell the user to kill
  the conflicting process before retrying.
- **Threading.** D-Bus signal callbacks and GTK UI must be on the GLib main loop.
  Use `dbus-next` (asyncio + GLib integration) or `pydbus` (which integrates with the
  default GLib MainLoop naturally) so we don't need separate threads. **Do not** spin up
  Python threads to handle D-Bus; it will deadlock or race the GTK UI.

## Reference implementations to read (not copy whole-hog, but consult)

- **taffybar / status-notifier-item** (Haskell, https://github.com/taffybar/status-notifier-item):
  authoritative reference for the Watcher and Host implementations. Read the protocol
  handling even if you don't read Haskell — the data flow is what matters.
- **polybar's tray module** (C++): a working SNI host in a panel context. Reference for
  edge cases in real-world tray behaviour.
- **AppIndicator3 / libayatana-appindicator** (C): the canonical client-side library.
  Understanding what it sends will inform what our Host needs to handle.
- The freedesktop.org SNI spec page itself for XML interface definitions.

## Code quality

- Heavy commenting. Anyone (the user, future-you) reading this in a month should understand
  what each D-Bus call does and why.
- A `--debug` flag that prints every D-Bus message sent and received, with timestamps.
- Constants at the top: bus names, object paths, icon size, window position/size.
- Wrap every D-Bus call in try/except with informative error logging. D-Bus errors are
  the #1 failure mode for this kind of code; never silently swallow them.

## Rollback safety

The script's startup banner (printed before the window appears) must include the exact
shell commands to:
- Kill TrayProbe (`pkill -f trayprobe.py`).
- Restart Cinnamon's panel (e.g. `cinnamon --replace &` — verify with the user first,
  may also need to re-enable specific applets in Cinnamon Settings).

The user is putting their working desktop's tray on the line to test this. Make the
rollback path obvious and printed every run.

## Success criteria

TrayProbe is "successful enough to greenlight the full panel project" if, with Cinnamon's
tray disabled:
- The window appears, the Watcher registers without conflict, the Host registers,
  the user's existing apps' icons populate the row, AND left-click activates at least
  the volume/network icons.

Anything beyond that (working right-click menus, perfect icon rendering, etc.) is gravy
and proves the project even more.

If the icons don't populate at all even after disabling Cinnamon's tray, the spike has
*failed in a useful way* — we learn that there's a deeper issue (maybe libappindicator
proxying, maybe a Watcher conflict from another process, maybe the apps in question
don't use SNI at all). Log loudly so we can diagnose.

## Out-of-scope reminder

Do not turn TrayProbe into "almost a panel." The temptation will be strong, especially
once tray icons are showing up — to start adding a clock, layout, styling, autohide. RESIST.
A polished TrayProbe is a wasted TrayProbe; the value is in finishing quickly with a
clear yes/no on the technical question. The full panel (PillPanel) is a separate project
with a separate spec that we will write after this one succeeds.
