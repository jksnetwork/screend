# SPDX-License-Identifier: GPL-3.0-or-later
"""Application entry point: a single resident instance driven by command-line actions.

The first `screend <action>` starts the service; later invocations forward their
arguments to it over D-Bus and exit. Staying resident keeps the clipboard
alive on Wayland and avoids start-up cost on every capture.
"""

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import __version__, capture, media, ocr, platform, scrolling  # noqa: E402
from .config import Config  # noqa: E402
from .editor import EditorWindow  # noqa: E402
from .history import HistoryWindow  # noqa: E402
from .pin import PinWindow  # noqa: E402
from .quickaccess import QuickAccess  # noqa: E402
from .selector import RegionSelector  # noqa: E402
from .settings import SettingsWindow  # noqa: E402
from .tray import Tray  # noqa: E402
from .welcome import WelcomeWindow  # noqa: E402
from .shot import HISTORY_DIR, Recording, Shot, prune_history, unique_path  # noqa: E402

APP_ID = "io.github.jksnetwork.Screend"

CSS = """
window.screend-selector { background: black; }
window.screend-quick-access { background: transparent; }
.screend-card {
  border-radius: 12px;
  background: @view_bg_color;
  box-shadow: 0 6px 24px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.12);
}
.screend-card-controls { background: rgba(0,0,0,0.45); }
.screend-canvas { background: mix(@window_bg_color, black, 0.25); }
.screend-swatch { min-width: 24px; min-height: 24px; padding: 3px; }
.screend-swatch:checked { box-shadow: inset 0 0 0 2px @accent_color; }
.screend-bg-swatch { padding: 3px; }
.screend-bg-swatch:checked { box-shadow: inset 0 0 0 2px @accent_color; }
window.screend-pin { background: transparent; }
.screend-pin-frame {
  margin: 10px;
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.18);
}
.screend-history-thumb { border-radius: 8px; background: alpha(@view_fg_color, 0.06); }
window.screend-scroll-panel { background: transparent; }
window.screend-scroll-panel > box {
  margin: 12px;
  border-radius: 14px;
  background: @window_bg_color;
  box-shadow: 0 6px 24px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.12);
}
"""

ACTIONS = ["region", "screen", "fullscreen", "last", "window", "text", "scroll", "record", "open",
           "history", "folder", "settings", "welcome", "launch", "daemon", "quit"]
CAPTURE_ACTIONS = ("region", "screen", "fullscreen", "last", "window", "text", "scroll", "record")


def build_parser():
    p = argparse.ArgumentParser(prog="screend", description="Screenshots, annotation and screen recording for Linux on Wayland")
    p.add_argument("action", nargs="?", default="region", choices=ACTIONS,
                   help="region (default): drag an area or click a window/screen | "
                        "screen: monitor under the pointer | fullscreen: all monitors | "
                        "last: repeat previous area | window: focused window | "
                        "text: copy the text in an area (OCR) | scroll: scrolling capture | "
                        "record: start/stop screen recording | open FILE: annotate an image | "
                        "history: browse captures | folder: open save folder | settings | "
                        "welcome: setup screen | launch: welcome on first run, else capture | "
                        "daemon: start in background | quit")
    p.add_argument("file", nargs="?", help="image to open with the 'open' action")
    p.add_argument("-d", "--delay", type=float, default=0, help="seconds to wait before capturing")
    p.add_argument("--screen", action="store_true", help="record: the whole current screen, no selection")
    return p


class ScreendApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.config = None
        self.quick_access = None
        self.busy = False
        self.last = None  # (backend, rect) of the previous area capture
        self.shell = capture.ShellHelper()
        self._recording = False
        self.purpose = "shot"
        self.scrolling = None
        self.tray = None
        self.portal_recorder = None
        self.history = None
        self.settings = None
        self.welcome = None
        self.pins = []

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # stay resident even with no windows open
        self.config = Config.load()
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.shell.subscribe("RecordingFinished", self.on_recording_finished)
        if self.config.show_tray:
            self.tray = Tray(self)
            self.tray.start()

    def do_command_line(self, cmdline):
        argv = cmdline.get_arguments()[1:]
        try:
            args = build_parser().parse_args(argv)
        except SystemExit:
            return 1
        cwd = cmdline.get_cwd()
        if args.action == "open" and args.file:
            args.file = str(Path(cwd or ".", args.file).expanduser().resolve())
        if args.delay > 0 and args.action in CAPTURE_ACTIONS:
            GLib.timeout_add(int(args.delay * 1000), lambda: self.run_action(args) and False)
        else:
            self.run_action(args)
        return 0

    def run_action(self, args):
        action = args.action
        if action == "quit":
            self.quit()
        elif action == "daemon":
            if not self.config.welcomed:
                # first login after installing: give the session a moment to settle
                GLib.timeout_add_seconds(3, lambda: self.open_welcome() and False)
        elif action == "welcome":
            self.open_welcome()
        elif action == "launch":
            if self.config.welcomed:
                self.capture("region")
            else:
                self.open_welcome()
        elif action == "folder":
            self.open_folder()
        elif action == "history":
            self.open_history()
        elif action == "settings":
            self.open_settings()
        elif action == "open":
            self.open_file(args.file)
        elif action == "record":
            self.toggle_recording(args.screen)
        elif action == "text":
            self.capture("region", purpose="text")
        elif action == "scroll":
            self.toggle_scrolling()
        else:
            self.capture(action)
        return True

    # -- capture flow -------------------------------------------------------

    def capture(self, mode, purpose="shot"):
        """purpose: "shot" (card/editor) or "text" (OCR the capture, copy the text)."""
        if self.busy or self.scrolling:
            return
        self.busy = True
        self.purpose = purpose
        if self.shell.available():
            self.capture_with_shell(mode)
        else:
            self.capture_with_portal(mode)

    def capture_failed(self, error):
        self.busy = False
        if error and "cancelled" not in str(error):
            message = error.message if isinstance(error, GLib.Error) else str(error)
            message = re.sub(r"^GDBus\.Error:[\w.]+:\s*", "", message)
            self.notify_error(f"Screenshot failed: {message}")

    def capture_with_shell(self, mode):
        """Fast path through the GNOME Shell extension (instant freeze, window snapping)."""
        def finish(path, rect=None):
            self.busy = False
            if not path:
                return
            if rect:
                self.last = ("shell", tuple(rect))
            if self.config.auto_copy and self.purpose == "shot":
                # the shell can set the clipboard without us having keyboard focus;
                # it reads the file, so only remove it once it has answered
                self.deliver(GdkPixbuf.Pixbuf.new_from_file(path), copy=False)
                self.shell.call("SetClipboardImage", GLib.Variant("(s)", (path,)),
                                lambda *_: capture.remove_quietly(path))
            else:
                self.deliver(capture.load_and_remove(path), copy=False)

        def on_result(result, error):
            if error:
                self.capture_failed(error)
            elif mode == "region":
                path, kind, rect = result
                finish(path, rect if kind != "window" else None)
            elif mode == "screen":
                finish(*result)
            else:
                finish(result[0])

        last = self.last[1] if self.last and self.last[0] == "shell" else None
        if mode == "last" and not last:
            mode = "region"
        if mode == "region":
            self.shell.call("SelectArea", GLib.Variant("(b)", (self.config.adjust_selection,)), on_result)
        elif mode == "last":
            self.shell.call("CaptureRect", GLib.Variant("((iiii))", (last,)), on_result)
        elif mode == "screen":
            self.shell.call("CaptureMonitor", None, on_result)
        elif mode == "window":
            self.shell.call("CaptureFocusedWindow", None, on_result)
        else:
            self.shell.call("CaptureAll", None, on_result)

    def capture_with_portal(self, mode):
        """Portable path: grim / KWin / the portal for pixels, plus our own GTK selector."""
        last = self.last[1] if self.last and self.last[0] == "portal" else None
        if mode == "last" and not last:
            mode = "region"
        kind = platform.desktop()

        if kind == "kde" and mode in ("window", "screen") and capture.KWin.usable:
            # KWin knows the active window/screen itself
            method = "CaptureActiveWindow" if mode == "window" else "CaptureActiveScreen"
            options = {"include-decoration": GLib.Variant("b", True)} if mode == "window" else None

            def on_kwin(pixbuf, error):
                if pixbuf:
                    self.busy = False
                    self.deliver(pixbuf)
                else:
                    self.capture_portable_fallback(mode, last)
            capture.KWin.capture(method, (), on_kwin, options)
            return
        self.capture_portable_fallback(mode, last)

    def capture_portable_fallback(self, mode, last):
        target = None  # a logical rect to crop, when the compositor tells us one
        if mode == "window":
            target = platform.focused_window_rect()
        elif mode == "screen":
            target = platform.focused_output_rect()
            pointer = platform.pointer_position()
            if target is None and pointer:
                mon = next((m for m in capture.monitor_layout()
                            if m.x <= pointer[0] < m.x + m.width and m.y <= pointer[1] < m.y + m.height), None)
                if mon:
                    target = (mon.x, mon.y, mon.width, mon.height)
        if mode in ("window", "screen") and target is None:
            # nothing to go on: GNOME's/the portal's own picker for windows, our selector for screens
            mode = "window-picker" if mode == "window" else "region"

        def on_grab(desktop, error=None):
            if error:
                self.capture_failed(error)
            elif target is not None:
                self.on_region(desktop, desktop.to_image(*target))
            elif mode == "region":
                RegionSelector(self, desktop, lambda rect: self.on_region(desktop, rect),
                               windows=platform.window_rects()).show()
            elif mode == "last":
                self.on_region(desktop, last)
            elif mode == "fullscreen":
                self.on_region(desktop, (0, 0, desktop.pixbuf.get_width(), desktop.pixbuf.get_height()))
            elif mode == "window-picker":
                # interactive portal results are already cropped
                self.busy = False
                self.deliver(desktop.pixbuf)

        capture.grab_desktop(on_grab, interactive=(mode == "window-picker"))

    def on_region(self, desktop, rect):
        self.busy = False
        if not rect:
            return
        x, y, w, h = rect
        x, y = max(0, x), max(0, y)
        w = min(w, desktop.pixbuf.get_width() - x)
        h = min(h, desktop.pixbuf.get_height() - y)
        if w < 2 or h < 2:
            return
        self.last = ("portal", (x, y, w, h))
        self.deliver(desktop.crop((x, y, w, h)))

    def deliver(self, pixbuf, copy=True):
        purpose, self.purpose = self.purpose, "shot"
        if purpose == "text":
            self.recognize_and_copy(pixbuf)
            return None
        shot = Shot(pixbuf, self.config)
        shot.keep_in_history_async()
        if self.config.auto_save:
            shot.save_async(lambda e: self.notify_error(f"Could not save screenshot: {e}"))
        if self.config.auto_copy and copy:
            shot.copy()
        if self.config.show_quick_access:
            if self.quick_access is None:
                self.quick_access = QuickAccess(self)
            self.quick_access.add_shot(shot)
        return shot

    # -- text recognition ---------------------------------------------------

    def recognize_and_copy(self, pixbuf, display=None):
        """OCR a capture and put the text on the clipboard."""
        if not ocr.available():
            self.notify_error(ocr.INSTALL_HINT)
            return
        self.toast("Reading text…")

        def done(text, error):
            if error:
                self.notify_error(f"Text recognition failed: {error}")
            elif not text:
                self.toast("No text found")
            elif display is not None:
                # called from a window the user just clicked: GTK may set the clipboard
                display.get_clipboard().set(text)
                self.toast(_preview("Copied text", text))
            elif self.shell.available():
                # from a hotkey nothing of ours has focus, so let the shell set it
                self.shell.call("SetClipboardText", GLib.Variant("(s)", (text,)))
                self.toast(_preview("Copied text", text))
            else:
                TextWindow(self, text).present()
        ocr.recognize(pixbuf, self.config.ocr_language, done)

    # -- scrolling capture ----------------------------------------------------

    def toggle_scrolling(self):
        if self.scrolling:
            self.scrolling.finish()
            return
        if self.busy:
            return
        grab = scrolling.area_grabber(self)
        if grab is None:
            self.notify_error("Scrolling capture needs the GNOME extension, grim (Sway/Hyprland) "
                              "or KDE fast capture.")
            return
        self.busy = True

        def begin(rect, first):
            self.busy = False
            if rect and rect[2] >= 8 and rect[3] >= 8:
                self.scrolling = scrolling.ScrollingCapture(self, rect, grab, first)
                self.scrolling.start()

        if self.shell.available():
            def picked(result, error):
                if error:
                    self.capture_failed(error)
                    return
                path, _kind, rect = result
                begin(rect, capture.load_and_remove(path) if path else None)
            self.shell.call("SelectArea", GLib.Variant("(b)", (self.config.adjust_selection,)), picked)
            return

        def on_grab(desktop, error=None):
            if error:
                self.capture_failed(error)
                return

            def selected(rect):
                if not rect:
                    self.busy = False
                    return
                x, y, w, h = rect
                s = desktop.scale  # image pixels -> logical desktop coordinates
                logical = (round(x / s + desktop.origin_x), round(y / s + desktop.origin_y),
                           round(w / s), round(h / s))
                begin(logical, desktop.crop(rect))
            RegionSelector(self, desktop, selected, windows=platform.window_rects()).show()
        capture.grab_desktop(on_grab)

    # -- recording ----------------------------------------------------------

    @property
    def recording(self):
        return self._recording

    @recording.setter
    def recording(self, value):
        if value != self._recording:
            self._recording = value
            if self.tray:
                self.tray.refresh()  # icon + "Stop Recording" menu item

    def toggle_recording(self, whole_screen=False):
        if not self.shell.available():
            self.toggle_portal_recording()
            return
        if self.recording:
            self.shell.call("StopRecording")
            return
        if self.busy:
            return
        self.busy = True

        def started(result, error):
            self.busy = False
            if error:
                self.capture_failed(error)
            else:
                self.recording = bool(result[0])
        mode = "screen" if whole_screen else "select"
        self.shell.call("StartRecording", GLib.Variant("(sbb)", (mode, self.config.adjust_selection,
                                                               self.config.record_cursor)), started)

    def toggle_portal_recording(self):
        """Recording via the ScreenCast portal (KDE, wlroots, GNOME without the extension)."""
        if self.portal_recorder is None:
            from .recorder import PortalRecorder
            self.portal_recorder = PortalRecorder(self.config)
        if self.portal_recorder.active:
            self.portal_recorder.stop()
            return
        if self.busy:
            return
        self.busy = True

        def started(ok, error):
            self.busy = False
            self.recording = ok
            if ok:
                self.toast("Recording… run the record shortcut again to stop")
            else:
                self.capture_failed(error)

        def finished(path):
            self.recording = False
            if path:
                self.on_recording_finished(path)
        self.portal_recorder.start(started, finished)

    def on_recording_finished(self, path):
        self.recording = False
        src = Path(path)
        if not src.exists():
            self.notify_error("The recording could not be found")
            return
        name = time.strftime(self.config.filename_format.replace("Screend", "Screend Recording"),
                             time.localtime())
        # like screenshots: history unless auto-save is on (or history is disabled)
        keep_in_history = not self.config.auto_save and self.config.history_limit > 0
        folder = HISTORY_DIR if keep_in_history else self.config.save_path
        try:
            folder.mkdir(parents=True, exist_ok=True)
            dst = unique_path(folder, Path(name).stem + src.suffix)
            shutil.move(str(src), dst)
        except OSError as e:
            self.notify_error(f"Could not save the recording: {e}")
            return
        if keep_in_history:
            prune_history(self.config.history_limit)

        def with_thumbnail(pixbuf):
            if pixbuf is None:
                pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 320, 180)
                pixbuf.fill(0x202024ff)
            rec = Recording(dst, pixbuf, self.config)
            if self.config.show_quick_access:
                if self.quick_access is None:
                    self.quick_access = QuickAccess(self)
                self.quick_access.add_shot(rec)
            else:
                self.toast(f"Saved {dst.name}")
        media.video_thumbnail(dst, with_thumbnail)

    def export_gif(self, recording, on_done=None):
        dst = recording.path.with_suffix(".gif")
        self.toast("Converting to GIF…")

        def done(error):
            if error:
                self.notify_error(f"GIF export failed: {error}")
            else:
                self.toast(f"Saved {dst.name}")
            if on_done:
                on_done(None if error else dst)
        media.video_to_gif(recording.path, dst, self.config.gif_fps, self.config.gif_max_width, done)

    # -- windows ------------------------------------------------------------

    def present(self, window):
        """Show a regular window, raised even when triggered from a global hotkey."""
        if self.shell.available():
            self.shell.call("ManageWindow", GLib.Variant("(ssii)", (window.get_title(), "focus", -1, -1)),
                            lambda *_: window.present())
        else:
            window.present()

    def open_editor(self, shot):
        self.present(EditorWindow(self, shot))

    def pin(self, shot):
        window = PinWindow(self, shot)
        self.pins.append(window)
        window.connect("destroy", lambda w: self.pins.remove(w) if w in self.pins else None)
        window.show_pin()

    def open_about(self):
        shell = "active" if self.shell.available() else "not running (log out and in after installing)"
        about = Adw.AboutWindow(
            application_name="Screend",
            application_icon="applets-screenshooter",
            version=__version__,
            comments="Capture, annotate, record and share screenshots on Linux.",
            website="https://github.com/jksnetwork/screend",
            issue_url="https://github.com/jksnetwork/screend/issues",
            license_type=Gtk.License.GPL_3_0,
            debug_info=(f"Screend {__version__}\n"
                        f"Desktop: {platform.desktop()} ({GLib.getenv('XDG_CURRENT_DESKTOP') or 'unknown'})\n"
                        f"GNOME extension: {shell}\n"
                        f"Tray icon: {'on' if self.tray else 'off'}\n"
                        f"Save folder: {self.config.save_path}\n"
                        f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}, "
                        f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}"),
            debug_info_filename="screend-debug.txt",
        )
        about.set_application(self)
        self.present(about)

    def open_folder(self):
        self.config.save_path.mkdir(parents=True, exist_ok=True)
        self.show_in_folder(self.config.save_path)

    def _live(self, window):
        """True while `window` is still open (closed windows leave the application's list)."""
        return window is not None and window in self.get_windows()

    def open_history(self):
        if self._live(self.history):
            self.history.reload()
        else:
            self.history = HistoryWindow(self)
        self.present(self.history)

    def open_welcome(self):
        if not self._live(self.welcome):
            self.welcome = WelcomeWindow(self)
        self.present(self.welcome)

    def open_settings(self):
        if not self._live(self.settings):
            self.settings = SettingsWindow(self)
        self.present(self.settings)

    def open_file(self, path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except (GLib.Error, TypeError) as e:
            self.notify_error(f"Could not open {path}: {e}")
            return
        shot = Shot(pixbuf, self.config)
        path = Path(path)
        if path.parent == HISTORY_DIR:
            shot.history_path = path  # Save should still go to the save folder
        else:
            shot.path = path
        self.open_editor(shot)

    def show_in_folder(self, path):
        path = Path(path)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))
        if path.is_file():
            launcher.open_containing_folder(None, None, None)
        else:
            launcher.launch(None, None, None)

    # -- feedback -----------------------------------------------------------

    def toast(self, message):
        n = Gio.Notification.new("Screend")
        n.set_body(message)
        n.set_priority(Gio.NotificationPriority.LOW)
        self.send_notification("status", n)
        GLib.timeout_add_seconds(4, lambda: self.withdraw_notification("status") or False)

    def notify_error(self, message):
        print(f"screend: {message}", file=sys.stderr)
        n = Gio.Notification.new("Screend")
        n.set_body(message)
        self.send_notification("error", n)


def main():
    # help is printed by this process: everything else is forwarded to the
    # resident instance, whose output the caller never sees
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        build_parser().print_help()
        return 0
    return ScreendApp().run(sys.argv)


def _preview(prefix, text, limit=60):
    flat = " ".join(text.split())
    return f"{prefix}: “{flat[:limit]}{'…' if len(flat) > limit else ''}”"


class TextWindow(Adw.Window):
    """Recognised text, for when it can't go straight to the clipboard."""

    def __init__(self, app, text):
        super().__init__(application=app, title="Recognised Text", default_width=520, default_height=360)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        copy_btn = Gtk.Button(label="Copy", css_classes=["suggested-action"])
        header.pack_end(copy_btn)
        view.add_top_bar(header)
        self.text_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=12, right_margin=12,
                                      top_margin=12, bottom_margin=12)
        self.text_view.get_buffer().set_text(text)
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.text_view)
        view.set_content(scroller)
        self.set_content(view)

        def copy(*_):
            buf = self.text_view.get_buffer()
            self.get_clipboard().set(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
            app.toast("Copied text")
            self.close()
        copy_btn.connect("clicked", copy)
