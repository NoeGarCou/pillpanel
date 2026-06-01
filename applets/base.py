import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


class Applet:
    """
    Base class for all PillPanel applets.

    Each applet provides one GTK widget that gets placed in the pill.
    It receives a reference to the PillPanel instance so it can access
    shared resources (D-Bus session bus, system bus, debug flag).

    Lifecycle:
        applet = MyApplet(panel)
        widget = applet.build()        # called once at startup
        panel.add_widget(widget, 'right')
        # ... applet runs via GLib timers / D-Bus signal subscriptions ...
        applet.destroy()               # called at shutdown
    """

    def __init__(self, panel):
        self.panel = panel

    def build(self) -> Gtk.Widget:
        """Return the GTK widget for this applet. Called exactly once."""
        raise NotImplementedError

    def destroy(self):
        """Clean up timers, subscriptions, etc. Called at shutdown."""
        pass
