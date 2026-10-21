# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture history: a thumbnail grid of everything in the save folder."""

import shutil
import threading
import time
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from . import media
from .imaging import cover_thumbnail
from .shot import HISTORY_DIR, unique_path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".webm", ".mp4", ".mkv"}
THUMB_W, THUMB_H = 240, 150
LIMIT = 300


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Screend History", default_width=1060, default_height=720)
        self.app = app
        self.folder = HISTORY_DIR

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Recent Captures")
        header.set_title_widget(self.title)
        folder_btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open saved screenshots folder")
        folder_btn.connect("clicked", lambda *_: app.show_in_folder(app.config.save_path))
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Settings")
        settings_btn.connect("clicked", lambda *_: app.open_settings())
        header.pack_end(settings_btn)
        header.pack_end(folder_btn)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (F5)")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)
        view.add_top_bar(header)

        self.flow = Gtk.FlowBox(valign=Gtk.Align.START, max_children_per_line=12, min_children_per_line=2,
                                selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                row_spacing=12, column_spacing=12,
                                margin_start=16, margin_end=16, margin_top=16, margin_bottom=16)
        self.flow.connect("child-activated", lambda _f, child: self.open_item(child.path))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.flow)
        self.empty = Adw.StatusPage(icon_name="image-x-generic-symbolic", title="No captures yet",
                                    description="Your most recent screenshots and recordings show up here.")
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "grid")
        self.stack.add_named(self.empty, "empty")
        view.set_content(self.stack)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.reload()

    def on_key(self, _ctl, keyval, _code, state):
        if keyval == Gdk.KEY_F5:
            self.reload()
        elif keyval == Gdk.KEY_Escape or (state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_w):
            self.close()
        else:
            return False
        return True

    def files(self):
        if not self.folder.is_dir():
            return []
        items = [p for p in self.folder.iterdir()
                 if p.is_file() and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT]
        items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return items[:LIMIT]

    def reload(self):
        while child := self.flow.get_first_child():
            self.flow.remove(child)
        files = self.files()
        self.stack.set_visible_child_name("grid" if files else "empty")
        self.title.set_subtitle(f"Last {len(files)} of {self.app.config.history_limit} kept  ·  "
                                "Save copies one to your screenshots folder")
        tiles = []
        for path in files:
            tile = _Tile(self, path)
            self.flow.append(tile)
            tiles.append(tile)
        threading.Thread(target=self.load_thumbnails, args=(tiles,), daemon=True).start()

    def load_thumbnails(self, tiles):
        for tile in tiles:
            if tile.path.suffix.lower() in VIDEO_EXT:
                GLib.idle_add(lambda t=tile: media.video_thumbnail(t.path, t.set_thumbnail) and False)
                continue
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(tile.path), THUMB_W * 2, THUMB_H * 2, True)
            except GLib.Error:
                continue
            GLib.idle_add(tile.set_thumbnail, pixbuf)

    def open_item(self, path):
        if path.suffix.lower() in IMAGE_EXT and path.suffix.lower() != ".gif":
            self.app.open_file(str(path))
        else:
            Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(self, None, None)


class _Tile(Gtk.FlowBoxChild):
    def __init__(self, window, path):
        super().__init__()
        self.window = window
        self.path = Path(path)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                   css_classes=["screend-history-thumb"])
        self.picture.set_size_request(THUMB_W, THUMB_H)
        overlay = Gtk.Overlay(child=self.picture)
        if self.path.suffix.lower() in VIDEO_EXT:
            badge = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=28,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["osd", "circular"])
            overlay.add_overlay(badge)
        box.append(overlay)
        stat = self.path.stat()
        box.append(Gtk.Label(label=self.path.stem, ellipsize=3, max_width_chars=28, xalign=0,
                             css_classes=["caption-heading"]))
        box.append(Gtk.Label(label=time.strftime("%b %d, %H:%M", time.localtime(stat.st_mtime)) +
                             f"  ·  {_size(stat.st_size)}", xalign=0, css_classes=["caption", "dim-label"]))
        self.set_child(box)

        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        drag.connect("prepare", self.on_drag_prepare)
        self.add_controller(drag)
        menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu_click.connect("pressed", self.on_menu)
        self.add_controller(menu_click)

        group = Gio.SimpleActionGroup()
        for name, cb in [("open", lambda: window.open_item(self.path)),
                         ("copy", self.copy),
                         ("save", self.save),
                         ("show", lambda: window.app.show_in_folder(self.path)),
                         ("trash", self.trash)]:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", lambda *_a, cb=cb: cb())
            group.add_action(action)
        self.insert_action_group("tile", group)

    def set_thumbnail(self, pixbuf):
        if pixbuf is not None:
            scale = self.get_scale_factor()
            thumb = cover_thumbnail(pixbuf, THUMB_W * scale, THUMB_H * scale)
            self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(thumb))
        return False

    def on_drag_prepare(self, _source, _x, _y):
        gfile = Gio.File.new_for_path(str(self.path))
        return Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_array([gfile]))

    def copy(self):
        from gi.repository import GObject
        clipboard = self.get_display().get_clipboard()
        if self.path.suffix.lower() in IMAGE_EXT and self.path.suffix.lower() != ".gif":
            texture = Gdk.Texture.new_from_filename(str(self.path))
            clipboard.set_content(Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture)))
        else:
            clipboard.set_content(self.on_drag_prepare(None, 0, 0))
        self.window.app.toast("Copied to clipboard")

    def save(self):
        folder = self.window.app.config.save_path
        try:
            folder.mkdir(parents=True, exist_ok=True)
            dst = unique_path(folder, self.path.name)
            shutil.copy2(self.path, dst)
        except OSError as e:
            self.window.app.notify_error(f"Could not save: {e}")
            return
        self.window.app.toast(f"Saved to {dst.name}")

    def trash(self):
        try:
            self.path.unlink()
        except OSError as e:
            self.window.app.notify_error(f"Could not remove: {e}")
            return
        self.window.flow.remove(self)

    def on_menu(self, _gesture, _n, x, y):
        menu = Gio.Menu()
        menu.append("Open", "tile.open")
        menu.append("Copy", "tile.copy")
        menu.append("Save to Screenshots Folder", "tile.save")
        menu.append("Show in Folder", "tile.show")
        menu.append("Remove from History", "tile.trash")
        popover = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        popover.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
