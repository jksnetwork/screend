# SPDX-License-Identifier: GPL-3.0-or-later
"""Tray icon via StatusNotifierItem + com.canonical.dbusmenu, over plain D-Bus.

Shown by KDE Plasma, Ubuntu's GNOME (AppIndicator extension), Waybar and most
other panels. Clicking the icon opens the menu; the icon turns into a red
record dot while a recording is running.
"""

import os

from gi.repository import Gio, GLib

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg name="revision" type="u"/><arg name="parent" type="i"/></signal>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
  </interface>
</node>
"""


class Tray:
    def __init__(self, app):
        self.app = app
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.revision = 1
        self.items = {}  # id -> (label, callback)
        self._reg_ids = []
        self._name_id = 0
        self._watch_id = 0

    # -- menu model ------------------------------------------------------------

    def entries(self):
        """(label, action) pairs; None is a separator."""
        app = self.app
        return [
            ("Capture Area", lambda: app.capture("region")),
            ("Capture Window", lambda: app.capture("window")),
            ("Capture Screen", lambda: app.capture("screen")),
            ("Capture All Screens", lambda: app.capture("fullscreen")),
            ("Capture Text", lambda: app.capture("region", purpose="text")),
            ("Finish Scrolling Capture" if app.scrolling else "Scrolling Capture", app.toggle_scrolling),
            None,
            ("Stop Recording" if app.recording else "Record Screen…", lambda: app.toggle_recording()),
            None,
            ("History", app.open_history),
            ("Open Screenshots Folder", app.open_folder),
            ("Settings", app.open_settings),
            ("About Screend", app.open_about),
            None,
            ("Quit Screend", app.quit),
        ]

    def menu_items(self):
        """[(id, {prop: Variant})] for the current state; ids start at 1 (0 is the root)."""
        out = []
        self.items = {}
        for i, entry in enumerate(self.entries(), start=1):
            if entry is None:
                out.append((i, {"type": GLib.Variant("s", "separator")}))
            else:
                label, callback = entry
                self.items[i] = callback
                out.append((i, {"label": GLib.Variant("s", label)}))
        return out

    def layout(self):
        children = [GLib.Variant("(ia{sv}av)", (i, props, [])) for i, props in self.menu_items()]
        return (0, {"children-display": GLib.Variant("s", "submenu")}, children)

    # -- lifecycle ---------------------------------------------------------------

    def start(self):
        item_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._reg_ids = [
            self.bus.register_object(ITEM_PATH, item_info, self._item_call, self._item_prop, None),
            self.bus.register_object(MENU_PATH, menu_info, self._menu_call, self._menu_prop, None),
        ]
        self._name_id = Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE,
                                                       lambda *_: self._register(), None)
        # panels restart (e.g. GNOME Shell reloads): register again whenever a watcher appears
        self._watch_id = Gio.bus_watch_name_on_connection(self.bus, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                                          lambda *_: self._register(), None)

    def stop(self):
        for reg in self._reg_ids:
            self.bus.unregister_object(reg)
        self._reg_ids = []
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0

    def _register(self):
        self.bus.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, -1, None, None)

    def refresh(self):
        """Call after app state changes (recording started/stopped)."""
        if not self._reg_ids:
            return
        self.revision += 1
        self.bus.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                             GLib.Variant("(ui)", (self.revision, 0)))
        self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon", None)
        self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)

    # -- org.kde.StatusNotifierItem ----------------------------------------------

    def _item_prop(self, _conn, _sender, _path, _iface, prop):
        recording = self.app.recording
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "screend"),
            "Title": GLib.Variant("s", "Screend"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", "media-record-symbolic" if recording else "applets-screenshooter-symbolic"),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], "Screend",
                                                      "Recording — click to stop" if recording else "Screenshots")),
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    def _item_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        # panels that send Activate instead of opening the menu: capture an area
        # (or stop the recording, the likeliest intent while one is running)
        if method == "Activate":
            GLib.idle_add(lambda: (self.app.toggle_recording() if self.app.recording
                                   else self.app.capture("region")) and False)
        invocation.return_value(None)

    # -- com.canonical.dbusmenu ----------------------------------------------------

    def _menu_prop(self, _conn, _sender, _path, _iface, prop):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(prop)

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self.layout())))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            wanted = [(i, props) for i, props in self.menu_items() if not ids or i in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (wanted,)))
        elif method == "GetProperty":
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._activate(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _data, _ts in params.unpack()[0]:
                if event_id == "clicked":
                    self._activate(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _activate(self, item_id):
        callback = self.items.get(item_id)
        if callback:
            # let the menu close first so it doesn't end up in the screenshot
            GLib.timeout_add(250, lambda: callback() and False)
