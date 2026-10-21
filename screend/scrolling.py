# SPDX-License-Identifier: GPL-3.0-or-later
"""Scrolling capture: grab one area repeatedly while the user scrolls, then stitch.

Needs a fast way to grab a small area: the GNOME extension, grim (wlroots) or
KWin. The portal is far too slow to keep up, so it isn't offered there.
"""

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from PIL import Image

from . import capture, platform
from .stitch import MAX_HEIGHT, Stitcher

INTERVAL_MS = 250
TITLE = "Screend Scrolling Capture"


def pixbuf_to_pil(pixbuf):
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    return Image.frombytes(mode, (pixbuf.get_width(), pixbuf.get_height()),
                           pixbuf.read_pixel_bytes().get_data(), "raw", mode, pixbuf.get_rowstride())


def pil_to_pixbuf(image):
    image = image.convert("RGB")
    w, h = image.size
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(image.tobytes()), GdkPixbuf.Colorspace.RGB,
                                           False, 8, w, h, w * 3)


def area_grabber(app):
    """A function grab(rect, callback(pixbuf or None)) for logical rects, or None if unsupported."""
    if app.shell.available():
        def grab_shell(rect, callback):
            def done(result, error):
                callback(None if error else capture.load_and_remove(result[0]))
            app.shell.call("CaptureRect", GLib.Variant("((iiii))", (tuple(rect),)), done)
        return grab_shell

    kind = platform.desktop()
    if kind in ("hyprland", "sway") and platform.have("grim"):
        def grab_grim(rect, callback):
            x, y, w, h = rect
            try:
                proc = Gio.Subprocess.new(["grim", "-g", f"{x},{y} {w}x{h}", "-t", "ppm", "-"],
                                          Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
            except GLib.Error:
                callback(None)
                return

            def done(p, res):
                try:
                    _ok, out, _err = p.communicate_finish(res)
                    loader = GdkPixbuf.PixbufLoader.new_with_type("pnm")
                    loader.write_bytes(out)
                    loader.close()
                    callback(loader.get_pixbuf())
                except GLib.Error:
                    callback(None)
            proc.communicate_async(None, None, done)
        return grab_grim

    if kind == "kde" and capture.KWin.usable:
        def grab_kwin(rect, callback):
            x, y, w, h = rect
            capture.KWin.capture("CaptureArea", (x, y, w, h), lambda pb, _err: callback(pb))
        return grab_kwin
    return None


class ScrollingCapture:
    def __init__(self, app, rect, grab, first=None):
        self.app = app
        self.rect = tuple(int(v) for v in rect)
        self.grab = grab
        self.stitcher = Stitcher()
        self.timer = None
        self.in_flight = False
        self.finished = False
        if first is not None:
            self.stitcher.add(pixbuf_to_pil(first))
        self.window = self.build_window()

    def build_window(self):
        win = Gtk.Window(application=self.app, title=TITLE, decorated=False, resizable=False)
        win.add_css_class("screend-scroll-panel")
        box = Gtk.Box(spacing=10, margin_start=14, margin_end=10, margin_top=10, margin_bottom=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.append(Gtk.Label(label="Scroll the highlighted area", xalign=0, css_classes=["heading"]))
        self.status = Gtk.Label(label="Waiting for you to scroll…", xalign=0, css_classes=["caption", "dim-label"])
        text.append(self.status)
        box.append(text)
        cancel = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Cancel (Esc)",
                            valign=Gtk.Align.CENTER, css_classes=["circular", "flat"])
        cancel.connect("clicked", lambda *_: self.cancel())
        done = Gtk.Button(label="Done", valign=Gtk.Align.CENTER, css_classes=["suggested-action", "pill"])
        done.connect("clicked", lambda *_: self.finish())
        box.append(done)
        box.append(cancel)
        win.set_child(box)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        win.add_controller(keys)
        win.connect("close-request", lambda *_: self.cancel() or True)

        layer = platform.layer_shell()
        if layer:
            layer.init_for_window(win)
            layer.set_namespace(win, "screend-scrolling")
            layer.set_layer(win, layer.Layer.OVERLAY)
            layer.set_anchor(win, layer.Edge.BOTTOM, True)
            layer.set_margin(win, layer.Edge.BOTTOM, 24)
            layer.set_keyboard_mode(win, layer.KeyboardMode.ON_DEMAND)
        return win

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.cancel()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.finish()
        else:
            return False
        return True

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        shell = self.app.shell
        if shell.available():
            shell.call("ShowFrame", GLib.Variant("((iiii))", (self.rect,)))
            # bottom-left, above everything, focus left with the app being scrolled
            shell.call("ManageWindow", GLib.Variant("(ssii)", (TITLE, "card", -1, -1)),
                       lambda *_: self.window.present())
        else:
            self.window.present()
        self.timer = GLib.timeout_add(INTERVAL_MS, self.tick)
        if self.app.tray:
            self.app.tray.refresh()

    def tick(self):
        if self.finished:
            return GLib.SOURCE_REMOVE
        if not self.in_flight:
            self.in_flight = True
            self.grab(self.rect, self.on_frame)
        return GLib.SOURCE_CONTINUE

    def on_frame(self, pixbuf):
        self.in_flight = False
        if self.finished or pixbuf is None:
            return
        added = self.stitcher.add(pixbuf_to_pil(pixbuf))
        height = self.stitcher.height
        if added:
            self.status.set_label(f"{height:,} px captured  ·  Enter to finish")
        if height >= MAX_HEIGHT:
            self.finish()

    def stop(self):
        self.finished = True
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.app.shell.available():
            self.app.shell.call("HideFrame")
        self.window.destroy()
        self.app.scrolling = None
        if self.app.tray:
            self.app.tray.refresh()

    def cancel(self):
        if not self.finished:
            self.stop()

    def finish(self):
        if self.finished:
            return
        self.stop()
        image = self.stitcher.result()
        if image is not None:
            self.app.deliver(pil_to_pixbuf(image))
