# SPDX-License-Identifier: GPL-3.0-or-later
"""Floating thumbnails shown after a capture (bottom-left of the current screen).

Wayland clients cannot position their own windows, so the cards live in a
transparent maximized window whose input region is shrunk to the cards;
clicks anywhere else fall through to the windows underneath.
"""

import cairo
from gi.repository import Gdk, Gio, GLib, Gtk

from . import platform
from .imaging import cover_thumbnail

CARD_WIDTH = 280
CARD_MAX_HEIGHT = 200
MAX_CARDS = 4


class QuickAccess(Gtk.Window):
    TITLE = "Screend Quick Access"

    def __init__(self, app):
        super().__init__(application=app, decorated=False, title=self.TITLE, resizable=False)
        self.app = app
        # GNOME extension: the shell places a card-sized window. Layer shell (KDE,
        # wlroots): we anchor it ourselves. Otherwise: maximized click-through window.
        self.layer = platform.layer_shell()
        if self.layer:
            self.layer.init_for_window(self)
            self.layer.set_namespace(self, "screend-quick-access")
            self.layer.set_layer(self, self.layer.Layer.OVERLAY)
            self.layer.set_anchor(self, self.layer.Edge.LEFT, True)
            self.layer.set_anchor(self, self.layer.Edge.BOTTOM, True)
            self.layer.set_keyboard_mode(self, self.layer.KeyboardMode.NONE)
        self.managed = bool(self.layer) or app.shell.available()
        self.add_css_class("screend-quick-access")
        margin = 16 if self.managed else 20
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                             halign=Gtk.Align.START, valign=Gtk.Align.END,
                             margin_start=margin, margin_bottom=margin,
                             margin_top=16 if self.managed else 0, margin_end=16 if self.managed else 0)
        self.set_child(self.cards)
        self._region = None
        if not self.managed:
            self.add_tick_callback(self._update_input_region)
        self.connect("close-request", self._on_close_request)

    def add_shot(self, shot):
        card = _Card(self, shot)
        self.cards.append(card)
        children = list(_children(self.cards))
        for old in children[:-MAX_CARDS]:
            old.dismiss()
        if not self.get_visible():
            self._region = None
            if self.layer:
                self.present()
            elif self.managed:
                # register the placement rule before the window exists, then show it
                self.app.shell.call("ManageWindow", GLib.Variant("(ssii)", (self.TITLE, "card", -1, -1)),
                                    lambda *_: self.present())
            else:
                self.maximize()
                self.present()
        return card

    def remove_card(self, card):
        self.cards.remove(card)
        if self.cards.get_first_child() is None:
            self.set_visible(False)
        elif self.managed:
            self.set_default_size(1, 1)  # shrink to the remaining cards

    def dismiss_all(self):
        for card in list(_children(self.cards)):
            card.dismiss()

    def _on_close_request(self, _w):
        self.dismiss_all()
        return True

    def _update_input_region(self, _widget, _clock):
        surface = self.get_surface()
        if surface is None or surface.get_width() <= 1 or self.cards.get_first_child() is None:
            return GLib.SOURCE_CONTINUE
        ok, bounds = self.cards.compute_bounds(self)
        if not ok:
            return GLib.SOURCE_CONTINUE
        rect = (int(bounds.get_x()) - 4, int(bounds.get_y()) - 4,
                int(bounds.get_width()) + 8, int(bounds.get_height()) + 8)
        if rect != self._region:
            self._region = rect
            surface.set_input_region(cairo.Region(cairo.RectangleInt(*rect)))
            # GTK only sends the region to the compositor with the next frame it draws
            self.queue_draw()
        return GLib.SOURCE_CONTINUE


class _Card(Gtk.Overlay):
    def __init__(self, qa, shot):
        super().__init__(css_classes=["screend-card"], overflow=Gtk.Overflow.HIDDEN)
        self.qa = qa
        self.app = qa.app
        self.shot = shot
        self.timeout_id = None
        self.hovered = False

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.set_child(self.picture)

        # hover controls
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["screend-card-controls"],
                                visible=False)
        top = Gtk.Box(margin_start=6, margin_top=6, margin_end=6)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Dismiss",
                           css_classes=["circular", "osd"], halign=Gtk.Align.START)
        close.connect("clicked", lambda *_: self.dismiss())
        top.append(close)
        spacer = Gtk.Box(hexpand=True)
        top.append(spacer)
        corner = Gtk.Box(spacing=6)
        if shot.is_video:
            gif = Gtk.Button(label="GIF", tooltip_text="Export as GIF", css_classes=["circular", "osd"])
            gif.connect("clicked", lambda *_: self.export_gif(gif))
            corner.append(gif)
        else:
            text = Gtk.Button(icon_name="insert-text-symbolic", tooltip_text="Copy text (OCR)",
                              css_classes=["circular", "osd"])
            text.connect("clicked", lambda *_: self.copy_text())
            corner.append(text)
            pin = Gtk.Button(icon_name="view-pin-symbolic", tooltip_text="Pin to screen",
                             css_classes=["circular", "osd"])
            pin.connect("clicked", lambda *_: self.pin())
            corner.append(pin)
            edit = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Annotate",
                              css_classes=["circular", "osd"])
            edit.connect("clicked", lambda *_: self.annotate())
            corner.append(edit)
        top.append(corner)
        self.controls.append(top)

        middle = Gtk.Box(spacing=8, vexpand=True, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
                         margin_bottom=16)
        copy_btn = Gtk.Button(label="Copy", css_classes=["pill", "osd"])
        copy_btn.connect("clicked", lambda *_: self.copy())
        middle.append(copy_btn)
        save_btn = Gtk.Button(label="Save", css_classes=["pill", "osd"])
        save_btn.connect("clicked", lambda *_: self.save())
        middle.append(save_btn)
        self.controls.append(middle)
        if shot.is_video:
            badge = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=22,
                              halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=8, margin_bottom=8,
                              css_classes=["osd", "circular"])
            self.add_overlay(badge)
        self.add_overlay(self.controls)

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self.on_enter)
        hover.connect("leave", self.on_leave)
        self.add_controller(hover)

        dbl = Gtk.GestureClick()
        dbl.connect("pressed", self.on_click)
        self.picture.add_controller(dbl)

        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        drag.connect("prepare", self.on_drag_prepare)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

        shot.listeners.append(self.on_shot_changed)
        self.on_shot_changed(shot)
        self.restart_timer()

    def on_shot_changed(self, shot):
        # a card-sized thumbnail; the full image would make the card as big as the capture
        w, h = shot.pixbuf.get_width(), shot.pixbuf.get_height()
        height = max(110, min(CARD_MAX_HEIGHT, round(CARD_WIDTH * h / max(w, 1))))
        scale = self.get_scale_factor()
        thumb = cover_thumbnail(shot.pixbuf, CARD_WIDTH * scale, height * scale)
        self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(thumb))
        self.picture.set_size_request(CARD_WIDTH, height)
        self.set_size_request(CARD_WIDTH, height)

    # -- auto-dismiss -------------------------------------------------------

    def restart_timer(self):
        self.stop_timer()
        secs = self.app.config.quick_access_timeout
        if secs > 0 and not self.hovered:
            self.timeout_id = GLib.timeout_add_seconds(secs, self._on_timeout)

    def stop_timer(self):
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
            self.timeout_id = None

    def _on_timeout(self):
        self.timeout_id = None
        self.dismiss()
        return GLib.SOURCE_REMOVE

    def on_enter(self, *_):
        self.hovered = True
        self.controls.set_visible(True)
        self.stop_timer()

    def on_leave(self, *_):
        self.hovered = False
        self.controls.set_visible(False)
        self.restart_timer()

    # -- actions ------------------------------------------------------------

    def on_click(self, gesture, n_press, _x, _y):
        if n_press == 2:
            self.open_video() if self.shot.is_video else self.annotate()

    def dismiss(self):
        self.stop_timer()
        if self.on_shot_changed in self.shot.listeners:
            self.shot.listeners.remove(self.on_shot_changed)
        if self.get_parent():
            self.qa.remove_card(self)

    def annotate(self):
        self.app.open_editor(self.shot)
        self.dismiss()

    def pin(self):
        self.app.pin(self.shot)
        self.dismiss()

    def copy_text(self):
        self.app.recognize_and_copy(self.shot.pixbuf, display=self.get_display())
        self.dismiss()

    def open_video(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(self.shot.path))).launch(None, None, None)
        self.dismiss()

    def export_gif(self, button):
        button.set_sensitive(False)
        button.set_label("…")
        self.stop_timer()
        self.hovered = True  # keep the card up while converting

        def done(gif_path):
            self.hovered = False
            button.set_sensitive(True)
            button.set_label("GIF")
            if gif_path:
                gfile = Gio.File.new_for_path(str(gif_path))
                self.get_display().get_clipboard().set_content(
                    Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_array([gfile])))
                self.app.toast(f"GIF copied: {gif_path.name}")
            self.restart_timer()
        self.app.export_gif(self.shot, done)

    def copy(self):
        self.shot.copy(self.get_display())
        self.app.toast("Copied to clipboard")
        self.dismiss()

    def save(self):
        path = self.shot.save()
        self.app.toast(f"Saved to {path.name}")
        self.dismiss()

    def on_drag_prepare(self, source, _x, _y):
        gfile = Gio.File.new_for_path(str(self.shot.temp_file()))
        source.set_icon(Gtk.WidgetPaintable.new(self.picture), CARD_WIDTH // 4, 20)
        if self.shot.is_video:
            return self.shot.content_provider()
        return Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_array([gfile])),
            self.shot.content_provider(),
        ])

    def on_drag_end(self, _source, _drag, _delete):
        self.dismiss()


def _children(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        yield child
        child = nxt
