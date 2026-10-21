# SPDX-License-Identifier: GPL-3.0-or-later
"""Grabbing the screen through the xdg-desktop-portal Screenshot interface.

On GNOME Wayland the portal is the only supported way for a regular app to read
the screen. We ask for a non-interactive shot of the whole desktop, load it,
and delete the file the portal wrote (it is only a transport).
"""

import itertools
import os
import threading
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import cairo
from gi.repository import Gdk, GdkPixbuf, Gio, GLib

from . import platform

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
_tokens = itertools.count(1)


@dataclass
class MonitorInfo:
    monitor: Gdk.Monitor
    x: int
    y: int
    width: int
    height: int


@dataclass
class Desktop:
    """A frozen image of the whole desktop plus the monitor layout it maps to."""

    pixbuf: GdkPixbuf.Pixbuf
    monitors: list
    origin_x: int  # logical coords of the top-left of the image
    origin_y: int
    scale: float  # image pixels per logical pixel

    def to_image(self, x, y, w, h):
        """Logical desktop rect -> integer image-pixel rect, clamped."""
        s = self.scale
        x0 = round((x - self.origin_x) * s)
        y0 = round((y - self.origin_y) * s)
        x1 = round((x + w - self.origin_x) * s)
        y1 = round((y + h - self.origin_y) * s)
        x0, x1 = max(0, x0), min(self.pixbuf.get_width(), x1)
        y0, y1 = max(0, y0), min(self.pixbuf.get_height(), y1)
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    def crop(self, rect):
        x, y, w, h = rect
        # copy() so the result does not pin the full-desktop buffer in memory
        return self.pixbuf.new_subpixbuf(x, y, w, h).copy()


def monitor_layout():
    display = Gdk.Display.get_default()
    monitors = display.get_monitors()
    out = []
    for i in range(monitors.get_n_items()):
        m = monitors.get_item(i)
        g = m.get_geometry()
        out.append(MonitorInfo(m, g.x, g.y, g.width, g.height))
    return out


class PortalError(Exception):
    pass


def grab_desktop(callback, interactive=False):
    """Freeze the whole desktop; calls callback(Desktop, None) or callback(None, error).

    interactive=True hands the choice to the desktop's own screenshot UI (portal).
    """
    if not interactive:
        kind = platform.desktop()
        if kind in ("hyprland", "sway") and platform.have("grim"):
            _grab_grim(callback)
            return
        if kind == "kde" and KWin.usable:
            KWin.capture("CaptureWorkspace", (), lambda pb, err: callback(_build_desktop(pb), None) if pb
                         else _grab_portal(callback))
            return
    _grab_portal(callback, interactive)


def _grab_grim(callback):
    # ppm skips compression entirely: by far the fastest format grim can write
    try:
        proc = Gio.Subprocess.new(["grim", "-t", "ppm", "-"],
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error as e:
        callback(None, e)
        return

    def done(p, res):
        try:
            _ok, out, _err = p.communicate_finish(res)
            loader = GdkPixbuf.PixbufLoader.new_with_type("pnm")
            loader.write_bytes(out)
            loader.close()
            callback(_build_desktop(loader.get_pixbuf()), None)
        except GLib.Error as e:
            _grab_portal(callback)
    proc.communicate_async(None, None, done)


def _grab_portal(callback, interactive=False):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    sender = bus.get_unique_name()[1:].replace(".", "_")
    token = f"screend{os.getpid()}_{next(_tokens)}"
    request_path = f"{PORTAL_PATH}/request/{sender}/{token}"
    sub_id = None

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        bus.signal_unsubscribe(sub_id)
        code, results = params.unpack()
        if code != 0:
            # 1 = user cancelled, 2 = denied/failed
            callback(None, PortalError("cancelled" if code == 1 else "screenshot denied by the desktop portal"))
            return
        path = unquote(urlparse(results["uri"]).path)
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error as e:
            callback(None, e)
            return
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        callback(_build_desktop(pixbuf), None)

    sub_id = bus.signal_subscribe(
        PORTAL_BUS, "org.freedesktop.portal.Request", "Response",
        request_path, None, Gio.DBusSignalFlags.NONE, on_response)

    def on_called(conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error as e:
            bus.signal_unsubscribe(sub_id)
            callback(None, e)

    options = {
        "handle_token": GLib.Variant("s", token),
        "interactive": GLib.Variant("b", interactive),
    }
    bus.call(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.portal.Screenshot", "Screenshot",
             GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
             Gio.DBusCallFlags.NONE, -1, None, on_called)


def _build_desktop(pixbuf):
    mons = monitor_layout()
    if not mons:
        return Desktop(pixbuf, [], 0, 0, 1.0)
    ox = min(m.x for m in mons)
    oy = min(m.y for m in mons)
    bw = max(m.x + m.width for m in mons) - ox
    scale = pixbuf.get_width() / bw if bw else 1.0
    return Desktop(pixbuf, mons, ox, oy, scale)


class ShellHelper:
    """Client for the Screend GNOME Shell extension (gnome-extension/), when it is running."""

    BUS_NAME = "io.github.jksnetwork.Screend.Shell"
    PATH = "/io/github/jksnetwork/Screend/Shell"
    IFACE = "io.github.jksnetwork.Screend.Shell"
    NO_TIMEOUT = 2**31 - 1  # selection waits on the user

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def available(self):
        if os.environ.get("SCREEND_NO_SHELL"):  # testing the portable paths on GNOME
            return False
        try:
            self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "GetNameOwner", GLib.Variant("(s)", (self.BUS_NAME,)),
                               None, Gio.DBusCallFlags.NONE, 500, None)
            return True
        except GLib.Error:
            return False

    def subscribe(self, signal, callback):
        """callback(*args) whenever the extension emits `signal`."""
        self.bus.signal_subscribe(self.BUS_NAME, self.IFACE, signal, self.PATH, None,
                                  Gio.DBusSignalFlags.NONE,
                                  lambda *a: callback(*a[5].unpack()))

    def call(self, method, params=None, callback=None):
        """Async call; callback(result_tuple, None) or callback(None, error)."""
        def done(bus, res):
            try:
                result = bus.call_finish(res).unpack()
            except GLib.Error as e:
                if callback:
                    callback(None, e)
                return
            if callback:
                callback(result, None)
        self.bus.call(self.BUS_NAME, self.PATH, self.IFACE, method, params, None,
                      Gio.DBusCallFlags.NO_AUTO_START, self.NO_TIMEOUT, None, done)


def load_and_remove(path):
    """Load a PNG the shell helper wrote, then delete it."""
    try:
        return GdkPixbuf.Pixbuf.new_from_file(path)
    finally:
        remove_quietly(path)


def remove_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


class KWin:
    """KDE Plasma's org.kde.KWin.ScreenShot2: fast, raw pixels over a pipe.

    KWin only answers apps whose desktop file lists the interface in
    X-KDE-DBUS-Restricted-Interfaces (install.sh --kde-fast-capture writes one).
    The first refusal disables this path and we use the portal instead.
    """

    usable = True
    # QImage::Format values KWin uses -> how to read them
    # (desktop captures are opaque, so straight and premultiplied alpha read the same)
    FORMATS = {4: cairo.FORMAT_RGB24, 5: cairo.FORMAT_ARGB32, 6: cairo.FORMAT_ARGB32,
               16: "rgba", 17: "rgba", 18: "rgba"}

    @classmethod
    def capture(cls, method, args, callback, options=None):
        """callback(pixbuf, None) or callback(None, error)."""
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        read_fd, write_fd = os.pipe()
        fds = Gio.UnixFDList.new()
        fd_index = fds.append(write_fd)
        os.close(write_fd)  # the list holds its own copy
        opts = {"include-cursor": GLib.Variant("b", False), "native-resolution": GLib.Variant("b", True)}
        opts.update(options or {})
        signature = {"CaptureWorkspace": "(a{sv}h)", "CaptureActiveScreen": "(a{sv}h)",
                     "CaptureActiveWindow": "(a{sv}h)", "CaptureArea": "(iiuua{sv}h)"}[method]
        params = GLib.Variant(signature, (*args, opts, fd_index))

        chunks = []
        reader = threading.Thread(target=lambda: chunks.append(_read_all(read_fd)), daemon=True)
        reader.start()

        def done(conn, res):
            # KWin has its copy of the write end now; drop ours or the read never ends
            for fd in fds.steal_fds():
                os.close(fd)
            try:
                result, _out_fds = conn.call_with_unix_fd_list_finish(res)
            except GLib.Error as e:
                if "AccessDenied" in e.message or "NoAuthorized" in e.message or "not authorized" in e.message:
                    cls.usable = False
                reader.join(0)
                callback(None, e)
                return
            info = result.unpack()[0]
            # the image arrives on the pipe after the reply; wait for it off the main loop
            def finish():
                reader.join(5)
                GLib.idle_add(lambda: callback(*cls._to_pixbuf(info, chunks[0] if chunks else b"")) and False)
            threading.Thread(target=finish, daemon=True).start()

        bus.call_with_unix_fd_list("org.kde.KWin", "/org/kde/KWin/ScreenShot2", "org.kde.KWin.ScreenShot2",
                                   method, params, GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE,
                                   10000, fds, None, done)

    @classmethod
    def _to_pixbuf(cls, info, data):
        try:
            w, h, stride, fmt = info["width"], info["height"], info["stride"], info["format"]
        except KeyError:
            return None, PortalError("unexpected reply from KWin")
        if len(data) < stride * h or fmt not in cls.FORMATS:
            return None, PortalError(f"unsupported KWin image (format {fmt})")
        kind = cls.FORMATS[fmt]
        if kind in (cairo.FORMAT_RGB24, cairo.FORMAT_ARGB32):
            # byte-for-byte the same layout as cairo's on little-endian machines
            surface = cairo.ImageSurface.create_for_data(bytearray(data), kind, w, h, stride)
            return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h), None
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                                 True, 8, w, h, stride)
        return pixbuf, None


def _read_all(fd):
    parts = []
    with os.fdopen(fd, "rb") as f:
        while chunk := f.read(1 << 20):
            parts.append(chunk)
    return b"".join(parts)
