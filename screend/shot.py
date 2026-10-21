# SPDX-License-Identifier: GPL-3.0-or-later
"""A single capture: its pixels, where it lives on disk, and clipboard/save helpers."""

import shutil
import threading
import time
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, GObject

from .imaging import pixbuf_to_png, png_to_pixbuf, png_to_texture

# Recent captures live here until pruned; only an explicit Save puts a file in save_dir.
HISTORY_DIR = Path(GLib.get_user_cache_dir()) / "screend" / "history"


def unique_path(folder, name):
    path = Path(folder) / name
    stem, suffix, n = path.stem, path.suffix, 2
    while path.exists():
        path = path.with_name(f"{stem} ({n}){suffix}")
        n += 1
    return path


def prune_history(limit):
    """Keep only the newest `limit` files in the history folder."""
    try:
        files = sorted((p for p in HISTORY_DIR.iterdir() if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in files[max(0, limit):]:
        try:
            old.unlink()
        except OSError:
            pass


class Shot:
    is_video = False

    def __init__(self, pixbuf, config):
        self.pixbuf = pixbuf
        self.config = config
        self.path = None          # where the user saved it (save_dir or Save As)
        self.history_path = None  # its copy in the recent-captures history
        self.created = time.time()
        self._png = None
        self._texture = None
        self._lock = threading.Lock()
        self.listeners = []  # called with (shot) when the image changes

    @property
    def png(self) -> bytes:
        with self._lock:
            if self._png is None:
                self._png = pixbuf_to_png(self.pixbuf)
            return self._png

    @property
    def texture(self):
        if self._texture is None:
            self._texture = Gdk.Texture.new_for_pixbuf(self.pixbuf)
        return self._texture

    def replace(self, png: bytes):
        """Swap in an edited image (e.g. annotated in the editor)."""
        with self._lock:
            self._png = png
        self.pixbuf = png_to_pixbuf(png)
        self._texture = png_to_texture(png)
        for path in (self.path, self.history_path):
            if path and path.exists():
                path.write_bytes(png)
        for cb in list(self.listeners):
            cb(self)

    def file_name(self):
        name = time.strftime(self.config.filename_format, time.localtime(self.created))
        return name if Path(name).suffix else name + ".png"

    def default_path(self) -> Path:
        return unique_path(self.config.save_path, self.file_name())

    def keep_in_history_async(self):
        """Add to recent-captures history (written off the main thread), then prune."""
        limit = self.config.history_limit
        if limit <= 0:
            return
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self.history_path = unique_path(HISTORY_DIR, self.file_name())

        def work():
            try:
                self.history_path.write_bytes(self.png)
                prune_history(limit)
            except OSError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def save(self) -> Path:
        if self.path is None:
            self.path = self.default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self.png)
        return self.path

    def save_async(self, on_error=None):
        """Pick the file name now, encode and write in a worker thread."""
        if self.path is None:
            self.path = self.default_path()

        def work():
            try:
                self.save()
            except OSError as e:
                if on_error:
                    GLib.idle_add(on_error, e)
        threading.Thread(target=work, daemon=True).start()
        return self.path

    def save_as(self, path):
        path = Path(path)
        path.write_bytes(self.png)
        return path

    def temp_file(self) -> Path:
        """A file to hand to drag-and-drop."""
        for path in (self.path, self.history_path):
            if path and path.exists():
                return path
        path = Path(GLib.get_user_cache_dir()) / "screend" / self.file_name()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png)
        return path

    def copy(self, display=None):
        display = display or Gdk.Display.get_default()
        display.get_clipboard().set_content(self.content_provider())

    def content_provider(self):
        # The GValue must be typed as GdkTexture: PyGObject would otherwise use the
        # concrete subclass, which GTK has no image/png serializer for.
        return Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, self.texture))


class Recording:
    """A finished screen recording; quacks enough like Shot for the quick access card."""

    is_video = True

    def __init__(self, path, thumbnail, config):
        self.path = Path(path)
        self.pixbuf = thumbnail
        self.config = config
        self.created = time.time()
        self.listeners = []
        self.saved_path = None
        self._texture = None

    @property
    def texture(self):
        if self._texture is None:
            self._texture = Gdk.Texture.new_for_pixbuf(self.pixbuf)
        return self._texture

    def temp_file(self):
        return self.path

    def save(self):
        """Copy into the save folder (recordings start out in history)."""
        if self.saved_path:
            return self.saved_path
        save_dir = self.config.save_path
        if self.path.parent == save_dir:
            self.saved_path = self.path
            return self.path
        save_dir.mkdir(parents=True, exist_ok=True)
        dst = unique_path(save_dir, self.path.name)
        shutil.copy2(self.path, dst)
        self.saved_path = dst
        return dst

    def content_provider(self):
        gfile = Gio.File.new_for_path(str(self.path))
        return Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_array([gfile]))

    def copy(self, display=None):
        """Copies the file itself; chat apps and file managers accept that as an attachment."""
        display = display or Gdk.Display.get_default()
        display.get_clipboard().set_content(self.content_provider())
