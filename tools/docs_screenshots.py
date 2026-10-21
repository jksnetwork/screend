# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate the screenshots in docs/images/.

Run inside a nested GNOME Shell so nothing personal ends up in the images:

    tools/nested-shell.sh python3 tools/docs_screenshots.py

Config and history go to a temporary directory; the demo window and its
contents are generated here.
"""

import os
import sys
import tempfile
from pathlib import Path

# isolate Screend's config/history before anything reads the XDG dirs
TMP = Path(tempfile.mkdtemp(prefix="screend-docs-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_CACHE_HOME"] = str(TMP / "cache")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)

from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from screend import annotations as A  # noqa: E402
from screend import capture, scrolling  # noqa: E402
from screend.app import ScreendApp  # noqa: E402
from screend.config import Config  # noqa: E402
from screend.editor import EditorWindow  # noqa: E402
from screend.quickaccess import _children  # noqa: E402
from screend.selector import RegionSelector  # noqa: E402
from screend.shot import HISTORY_DIR, Shot  # noqa: E402

MAX_WIDTH = 1400
NOTES = """Lisbon trip — June

1. Book tram 28 tickets
2. Confirm the Alfama apartment
3. Reserve dinner for Saturday
4. Pack a travel adapter

Still to decide
• Day trip to Sintra or Cascais?
• Fado show on Friday or Saturday?
• Rent bikes along the river

Contacts
  Apartment host ....... host@example.com
  Walking tours ........ tours@example.com
"""

PLACES = ["Belém Tower", "Time Out Market", "LX Factory", "Miradouro da Graça", "Jerónimos Monastery",
          "Praça do Comércio", "São Jorge Castle", "Bairro Alto", "Feira da Ladra", "Gulbenkian Museum",
          "Pastéis de Belém", "Oceanário", "Santa Justa Lift", "Rua Augusta Arch", "Cais do Sodré"]
ITINERARY = [f"Day {1 + i // 6} · {9 + (i % 6) * 2:02d}:00   {PLACES[(i * 7) % len(PLACES)]} — stop {i + 1}"
             for i in range(150)]


# -- demo content ----------------------------------------------------------------

class DemoWindow(Adw.ApplicationWindow):
    """A neutral-looking app to take screenshots of."""

    def __init__(self, app, title="Trip Notes", text=NOTES, lines=None):
        super().__init__(application=app, title=title, default_width=1100, default_height=700)
        split = Adw.NavigationSplitView()
        side = Adw.ToolbarView()
        side.add_top_bar(Adw.HeaderBar(show_title=False))
        items = Gtk.ListBox(css_classes=["navigation-sidebar"])
        for name, icon in [("Lisbon", "mark-location-symbolic"), ("Packing", "emblem-ok-symbolic"),
                           ("Calendar", "x-office-calendar-symbolic"), ("Favourites", "starred-symbolic"),
                           ("Archive", "folder-symbolic")]:
            row = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8, margin_start=6)
            row.append(Gtk.Image(icon_name=icon))
            row.append(Gtk.Label(label=name, xalign=0))
            items.append(row)
        items.select_row(items.get_row_at_index(0))
        side.set_content(items)
        split.set_sidebar(Adw.NavigationPage(title="Notes", child=side))

        content = Adw.ToolbarView()
        content.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title, subtitle="Edited just now")))
        self.view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD,
                                 left_margin=36, right_margin=36, top_margin=28, pixels_below_lines=6)
        self.view.add_css_class("docs-notes")
        self.view.get_buffer().set_text(text if lines is None else "\n".join(lines))
        self.scroller = Gtk.ScrolledWindow(child=self.view)
        content.set_content(self.scroller)
        split.set_content(Adw.NavigationPage(title=title, child=content))
        self.set_content(split)


CSS = ".docs-notes { font-size: 15pt; }"


# -- helpers -------------------------------------------------------------------

def save(pixbuf, name, max_width=MAX_WIDTH):
    if pixbuf is None:
        print("skipped", name, flush=True)
        return
    w, h = pixbuf.get_width(), pixbuf.get_height()
    if w > max_width:
        pixbuf = pixbuf.scale_simple(max_width, round(h * max_width / w), GdkPixbuf.InterpType.HYPER)
    pixbuf.savev(str(OUT / name), "png", ["compression"], ["9"])
    print("saved", name, pixbuf.get_width(), "x", pixbuf.get_height(), flush=True)


def crop(pixbuf, x, y, w, h):
    return pixbuf.new_subpixbuf(x, y, w, h).copy()


class Script:
    """Runs a generator of steps; a step yields ("wait", ms) or ("shot", method)."""

    def __init__(self, app, gen):
        self.app, self.gen = app, gen
        GLib.timeout_add(1500, self.step, None)

    def step(self, value):
        try:
            cmd = self.gen.send(value)
        except StopIteration:
            self.app.quit()
            return False
        if cmd[0] == "wait":
            GLib.timeout_add(cmd[1], self.step, None)
        elif cmd[0] == "until":
            # poll a condition (e.g. a D-Bus signal having arrived), at most cmd[2] ms
            deadline = [cmd[2]]

            def poll():
                deadline[0] -= 200
                if cmd[1]() or deadline[0] <= 0:
                    self.step(None)
                    return False
                return True
            GLib.timeout_add(200, poll)
        else:
            def done(result, error):
                if error:
                    print("capture failed:", error, flush=True)
                    self.step(None)
                else:
                    self.step(capture.load_and_remove(result[0]))
            self.app.shell.call(cmd[1], None, done)
        return False


def beautify(app, pixbuf, style, padding=56, radius=12):
    """Run a picture through Screend's own background/shadow export."""
    cfg = app.config
    saved = (cfg.bg_style, cfg.bg_padding, cfg.bg_radius, cfg.bg_shadow)
    cfg.bg_style, cfg.bg_padding, cfg.bg_radius, cfg.bg_shadow = style, padding, radius, True
    ed = EditorWindow(app, Shot(pixbuf, cfg))
    png = ed.export_png()
    ed.dirty = False
    ed.destroy()
    cfg.bg_style, cfg.bg_padding, cfg.bg_radius, cfg.bg_shadow = saved
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png)
    loader.close()
    return loader.get_pixbuf()


# -- the scenes ------------------------------------------------------------------

def scenes(app):
    css = Gtk.CssProvider()
    css.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    demo = DemoWindow(app)
    demo.maximize()
    demo.present()
    yield ("wait", 2000)
    desktop_pb = yield ("shot", "CaptureAll")
    panel = 32  # GNOME's top bar in the nested session
    width, height = desktop_pb.get_width(), desktop_pb.get_height()
    notes = crop(desktop_pb, 0, panel, width, height - panel)

    # 1. area selection over the demo app
    desktop = capture._build_desktop(desktop_pb)
    sel = RegionSelector(app, desktop, lambda rect: None)
    sel.show()
    yield ("wait", 1200)
    sel.drag_start, sel.drag_end = (330, 150), (1210, 560)
    sel.pointer = sel.drag_end
    sel.redraw()
    yield ("wait", 600)
    pb = yield ("shot", "CaptureAll")
    save(pb, "selector.png")
    sel.finish(None)
    yield ("wait", 800)

    # 2. the quick access card, hovered
    card_shot = crop(notes, 330, 150 - panel, 880, 410)
    app.deliver(card_shot)
    yield ("wait", 1500)
    for card in _children(app.quick_access.cards):
        card.on_enter()
    yield ("wait", 500)
    pb = yield ("shot", "CaptureAll")
    save(crop(pb, 0, height - 380, 680, 380), "card.png")
    app.quick_access.dismiss_all()
    yield ("wait", 800)

    # 3. the editor with annotations
    # the notes text, below the window's header bar (positions match NOTES' line layout)
    shot = Shot(crop(notes, 350, 45, 1000, 560), app.config)
    ed = EditorWindow(app, shot)
    ed.shapes += [
        _highlight("#ffcc00", 5, (28, 43), (250, 43)),
        _two(A.Rect, "#ff3b30", 5, (18, 148), (470, 212)),
        _two(A.Arrow, "#ff3b30", 5, (700, 115), (482, 176)),
        A.Text("#ff3b30", 5, 560, 64, "Do this week", filled=True),
        A.Counter("#007aff", 5, 450, 290, 1),
        A.Counter("#007aff", 5, 450, 321, 2),
        A.Counter("#007aff", 5, 450, 352, 3),
        _two(A.Blur, "#000000", 5, (210, 428), (470, 494)),
    ]
    ed.maximize()
    app.present(ed)
    yield ("wait", 2000)
    pb = yield ("shot", "CaptureAll")
    save(crop(pb, 0, panel, width, height - panel), "editor.png")

    # 4. the background panel
    ed.bg_buttons["grape"].set_active(True)
    ed.bg_btn.popup()
    yield ("wait", 1200)
    pb = yield ("shot", "CaptureAll")
    save(crop(pb, 0, panel, width, height - panel), "background.png")
    ed.bg_btn.popdown()

    # 5. what the export looks like
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(ed.export_png())
    loader.close()
    exported = loader.get_pixbuf()
    save(exported, "beautified.png", max_width=1100)
    ed.dirty = False
    ed.destroy()
    yield ("wait", 600)

    # 6. a pinned screenshot over the app
    app.pin(Shot(exported.scale_simple(exported.get_width() // 2, exported.get_height() // 2,
                                       GdkPixbuf.InterpType.HYPER), app.config))
    yield ("wait", 1500)
    pb = yield ("shot", "CaptureAll")
    save(pb, "pin.png")
    for pin in list(app.pins):
        pin.close()
    demo.close()
    yield ("wait", 800)

    # 7. scrolling capture in progress, then its result
    long_doc = DemoWindow(app, title="Itinerary", lines=ITINERARY)
    long_doc.maximize()
    long_doc.present()
    yield ("wait", 2000)
    results = []
    deliver = app.deliver
    app.deliver = lambda pixbuf, copy=True: (results.append(pixbuf), deliver(pixbuf, copy))
    app.shell.call("StartRecording", GLib.Variant("(sbb)", ("screen", False, False)))
    yield ("wait", 1200)
    grab = scrolling.area_grabber(app)
    app.scrolling = scrolling.ScrollingCapture(app, (300, 140, 900, 560), grab)
    app.scrolling.start()
    yield ("wait", 1200)
    adj = long_doc.scroller.get_vadjustment()
    for i in range(40):  # gentle, steady scrolling reads better in the GIF
        yield ("wait", 110)
        adj.set_value(adj.get_value() + 38)
        if i == 30:
            pb = yield ("shot", "CaptureAll")
            save(pb, "scrolling.png")
    yield ("wait", 900)
    app.scrolling.finish()
    yield ("wait", 1200)
    for card in _children(app.quick_access.cards):
        card.on_enter()
    yield ("wait", 2200)
    app.shell.call("StopRecording")
    yield ("until", lambda: RECORDINGS, 15000)
    if RECORDINGS:
        make_gif(RECORDINGS[0], OUT / "scrolling.gif")
    app.quick_access.dismiss_all()
    if results:
        tall = results[0]
        save(crop(tall, 0, 0, tall.get_width(), min(tall.get_height(), 1800)), "scrolling-result.png", max_width=600)
    long_doc.close()
    yield ("wait", 800)

    # 8. history and settings (window-only shots, framed with Screend's own background)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(["selector", "editor", "beautified", "pin", "scrolling", "card", "background"]):
        src = OUT / f"{name}.png"
        if src.exists():
            dst = HISTORY_DIR / f"Screend 2026-10-21 at 10.{10 + i:02d}.00.png"
            dst.write_bytes(src.read_bytes())
    app.open_history()
    yield ("wait", 2500)
    pb = yield ("shot", "CaptureFocusedWindow")
    save(pb and beautify(app, pb, "ocean"), "history.png", max_width=1200)
    app.history.close()
    yield ("wait", 600)

    app.open_settings()
    yield ("wait", 1500)
    pb = yield ("shot", "CaptureFocusedWindow")
    save(pb and beautify(app, pb, "mint"), "settings.png", max_width=900)
    app.settings.close()


RECORDINGS = []


def make_gif(video, dst, width=800, fps=10):
    """Video -> small looping GIF (palette tuned to the clip; only changed areas re-dithered)."""
    import subprocess
    vf = (f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=96:stats_mode=diff[p];"
          "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", vf, "-loop", "0", str(dst)], check=True)
    print("saved", dst.name, f"{dst.stat().st_size // 1024} KB", flush=True)


def _two(cls, color, width, p0, p1):
    shape = cls(color, width, *p0)
    shape.drag_to(*p1)
    return shape


def _highlight(color, width, p0, p1):
    shape = A.Highlighter(color, width, *p0)
    shape.drag_to(*p1)
    return shape


def main():
    # the recording becomes the GIF, not a history item/card
    ScreendApp.on_recording_finished = lambda self, path: RECORDINGS.append(path)
    # nothing is auto-saved during the run; this is just what Settings displays
    cfg = Config(save_dir="~/Pictures/Screend", show_tray=False, quick_access_timeout=0, history_limit=50)
    cfg.save()
    app = ScreendApp()
    if not app.shell.available():
        print("The Screend GNOME extension isn't running; use tools/nested-shell.sh", file=sys.stderr)
    GLib.idle_add(lambda: Script(app, scenes(app)) and False)
    app.run(["screend", "daemon"])


if __name__ == "__main__":
    main()
