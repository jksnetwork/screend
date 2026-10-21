# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned screenshots: small frameless windows kept above everything else.

Drag to move · scroll to resize · Ctrl+scroll for opacity · double-click or Esc
to close · right-click for actions. Keeping the window on top needs the GNOME
extension (or a compositor rule elsewhere); without it the pin is a normal window.
"""

import itertools

import cairo
from gi.repository import Gdk, Gio, GLib, Gtk

from . import platform
from .imaging import pixbuf_to_surface

_ids = itertools.count(1)
MAX_INITIAL_FRACTION = 0.5


class PinWindow(Gtk.Window):
    def __init__(self, app, shot):
        self.pin_id = next(_ids)
        # non-resizable windows follow their content's size, so scrolling can shrink them
        super().__init__(application=app, decorated=False, resizable=False,
                         title=f"Screend Pin {self.pin_id}")
        self.app = app
        self.shot = shot
        self.add_css_class("screend-pin")
        self.img_w, self.img_h = shot.pixbuf.get_width(), shot.pixbuf.get_height()
        self.scale = self.initial_scale()

        self.surface = pixbuf_to_surface(shot.pixbuf)
        self.picture = Gtk.DrawingArea(css_classes=["screend-pin-picture"])
        self.picture.set_draw_func(self.draw)
        frame = Gtk.Box(css_classes=["screend-pin-frame"], overflow=Gtk.Overflow.HIDDEN)
        frame.append(self.picture)
        self.set_child(frame)
        self.apply_size()

        # on KDE/wlroots a layer surface stays on top; we position it via margins
        self.layer = platform.layer_shell()
        if self.layer:
            self.layer.init_for_window(self)
            self.layer.set_namespace(self, "screend-pin")
            self.layer.set_layer(self, self.layer.Layer.TOP)
            self.layer.set_anchor(self, self.layer.Edge.LEFT, True)
            self.layer.set_anchor(self, self.layer.Edge.TOP, True)
            self.layer.set_keyboard_mode(self, self.layer.KeyboardMode.ON_DEMAND)
            self.margins = [120, 120]
            self.apply_margins()

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        self.picture.add_controller(drag)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", lambda _g, n, *_: n == 2 and self.close())
        self.picture.add_controller(click)

        menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu_click.connect("pressed", self.on_menu)
        self.picture.add_controller(menu_click)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        group = Gio.SimpleActionGroup()
        for name, cb in [("copy", lambda: shot.copy(self.get_display())),
                         ("annotate", lambda: (app.open_editor(shot), self.close())),
                         ("close", self.close)]:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", lambda *_a, cb=cb: cb())
            group.add_action(action)
        self.insert_action_group("pin", group)
        shot.listeners.append(self.on_shot_changed)
        self.connect("close-request", self.on_close_request)

    def initial_scale(self):
        monitors = Gdk.Display.get_default().get_monitors()
        if not monitors.get_n_items():
            return 1.0
        g = monitors.get_item(0).get_geometry()
        return min(1.0, g.width * MAX_INITIAL_FRACTION / self.img_w, g.height * MAX_INITIAL_FRACTION / self.img_h)

    def apply_size(self):
        self.picture.set_content_width(max(40, round(self.img_w * self.scale)))
        self.picture.set_content_height(max(30, round(self.img_h * self.scale)))

    def draw(self, _area, cr, width, height):
        cr.scale(width / self.img_w, height / self.img_h)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()

    def show_pin(self):
        if not self.layer and self.app.shell.available():
            # keep above other windows on every workspace
            self.app.shell.call("ManageWindow", GLib.Variant("(ssii)", (self.get_title(), "pin", -1, -1)),
                                lambda *_: self.present())
        else:
            self.present()

    def on_shot_changed(self, shot):
        self.surface = pixbuf_to_surface(shot.pixbuf)
        self.img_w, self.img_h = shot.pixbuf.get_width(), shot.pixbuf.get_height()
        self.apply_size()
        self.picture.queue_draw()

    def on_close_request(self, _w):
        if self.on_shot_changed in self.shot.listeners:
            self.shot.listeners.remove(self.on_shot_changed)
        return False

    def apply_margins(self):
        self.layer.set_margin(self, self.layer.Edge.LEFT, max(0, round(self.margins[0])))
        self.layer.set_margin(self, self.layer.Edge.TOP, max(0, round(self.margins[1])))

    def on_drag_update(self, _gesture, dx, dy):
        if self.layer:
            self.margins = [self._drag_margins[0] + dx, self._drag_margins[1] + dy]
            self.apply_margins()

    def on_drag_begin(self, gesture, x, y):
        if self.layer:
            self._drag_margins = list(self.margins)
            return
        # hand the move over to the compositor (the only way to move a window on Wayland)
        surface = self.get_surface()
        event = gesture.get_last_event(gesture.get_current_sequence())
        if surface and event:
            surface.begin_move(gesture.get_device(), Gdk.BUTTON_PRIMARY, x, y, event.get_time())
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_scroll(self, ctl, _dx, dy):
        if ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.set_opacity(min(1.0, max(0.2, self.get_opacity() - dy * 0.1)))
        else:
            self.scale = min(4.0, max(0.1, self.scale * (1.1 ** -dy)))
            self.apply_size()
        return True

    def on_key(self, _ctl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
        elif state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.shot.copy(self.get_display())
        else:
            return False
        return True

    def on_menu(self, gesture, _n, x, y):
        menu = Gio.Menu()
        menu.append("Copy", "pin.copy")
        menu.append("Annotate", "pin.annotate")
        menu.append("Close", "pin.close")
        popover = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        popover.set_parent(self.picture)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()
