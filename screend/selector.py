# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-screen region picker drawn over a frozen screenshot, one window per monitor.

Drag to select (selection may span monitors), click to take a whole monitor,
Enter for the monitor under the pointer, Esc to cancel.
"""

import math

import cairo
from gi.repository import Gdk, Gtk, Pango, PangoCairo

from .imaging import pixbuf_to_surface

DIM = (0, 0, 0, 0.45)
ACCENT = (0.25, 0.6, 1.0)
LOUPE_SRC = 15  # logical px sampled around the pointer
LOUPE_ZOOM = 8
CLICK_SLOP = 4


class RegionSelector:
    def __init__(self, app, desktop, on_done, windows=()):
        self.app = app
        self.desktop = desktop
        self.on_done = on_done
        self.windows = list(windows)  # logical (x, y, w, h), topmost first
        self.pointer = None  # global logical coords
        self.drag_start = None
        self.drag_end = None
        self.finished = False
        self.windows = []
        s = desktop.scale
        for mon in desktop.monitors:
            rect = desktop.to_image(mon.x, mon.y, mon.width, mon.height)
            surface = pixbuf_to_surface(desktop.pixbuf.new_subpixbuf(*rect))
            self.windows.append(_MonitorWindow(self, mon, surface, s))

    def show(self):
        for w in self.windows:
            w.present()
            w.fullscreen_on_monitor(w.mon.monitor)

    # -- state shared by all monitor windows -------------------------------

    def selection(self):
        if not (self.drag_start and self.drag_end):
            return None
        (x0, y0), (x1, y1) = self.drag_start, self.drag_end
        return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)

    def redraw(self):
        for w in self.windows:
            w.area.queue_draw()

    def monitor_at(self, gx, gy):
        for mon in self.desktop.monitors:
            if mon.x <= gx < mon.x + mon.width and mon.y <= gy < mon.y + mon.height:
                return mon
        return None

    def finish(self, logical_rect):
        if self.finished:
            return
        self.finished = True
        for w in self.windows:
            w.destroy()
        rect = None
        if logical_rect:
            rect = self.desktop.to_image(*logical_rect)
            if rect[2] < 2 or rect[3] < 2:
                rect = None
        self.on_done(rect)

    def finish_monitor(self, mon):
        self.finish((mon.x, mon.y, mon.width, mon.height) if mon else None)

    def window_at(self, gx, gy):
        for rect in self.windows:
            x, y, w, h = rect
            if x <= gx < x + w and y <= gy < y + h:
                return rect
        return None

    def finish_click(self, gx, gy, fallback_mon):
        """A click takes the window under the pointer, or else the whole screen."""
        rect = self.window_at(gx, gy)
        if rect:
            self.finish(rect)
        else:
            self.finish_monitor(self.monitor_at(gx, gy) or fallback_mon)


class _MonitorWindow(Gtk.Window):
    def __init__(self, selector, mon, surface, scale):
        super().__init__(application=selector.app, decorated=False, title="Screend selection")
        self.sel = selector
        self.mon = mon
        self.surface = surface
        self.scale = scale
        self.add_css_class("screend-selector")

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self.draw)
        self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self.set_child(self.area)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        motion.connect("leave", self.on_leave)
        self.area.add_controller(motion)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)

        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda *a: self.sel.finish(None))
        self.area.add_controller(right)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def to_global(self, x, y):
        return self.mon.x + x, self.mon.y + y

    # -- input --------------------------------------------------------------

    def on_motion(self, _ctl, x, y):
        if self.sel.drag_start is None:
            self.sel.pointer = self.to_global(x, y)
            self.sel.redraw()

    def on_leave(self, _ctl):
        pass

    def on_drag_begin(self, _gesture, x, y):
        self.sel.drag_start = self.to_global(x, y)
        self.sel.drag_end = self.sel.drag_start
        self.sel.redraw()

    def on_drag_update(self, gesture, dx, dy):
        sx, sy = self.sel.drag_start
        # with an implicit grab the offsets keep coming beyond this monitor
        self.sel.drag_end = (sx + dx, sy + dy)
        self.sel.pointer = self.sel.drag_end
        self.sel.redraw()

    def on_drag_end(self, _gesture, dx, dy):
        if math.hypot(dx, dy) < CLICK_SLOP:
            self.sel.finish_click(*self.sel.drag_start, self.mon)
            return
        sx, sy = self.sel.drag_start
        self.sel.drag_end = (sx + dx, sy + dy)
        self.sel.finish(self.sel.selection())

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.sel.finish(None)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            p = self.sel.pointer
            if p:
                self.sel.finish_click(*p, self.mon)
            else:
                self.sel.finish_monitor(self.mon)
            return True
        return False

    # -- drawing ------------------------------------------------------------

    def draw(self, _area, cr, width, height):
        cr.save()
        cr.scale(1 / self.scale, 1 / self.scale)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        cr.restore()

        sel = self.sel.selection()
        local = None
        if sel:
            local = (sel[0] - self.mon.x, sel[1] - self.mon.y, sel[2], sel[3])
        hovered = None
        if not sel and self.sel.pointer:
            win = self.sel.window_at(*self.sel.pointer)
            if win:
                hovered = (win[0] - self.mon.x, win[1] - self.mon.y, win[2], win[3])
                local = hovered

        # dim everything outside the selection
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, width, height)
        if local:
            cr.rectangle(*local)
        cr.set_source_rgba(*DIM)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)

        if hovered:
            cr.set_line_width(3)
            cr.set_source_rgba(*ACCENT, 0.9)
            cr.rectangle(*hovered)
            cr.stroke()
            px, py = self.sel.pointer[0] - self.mon.x, self.sel.pointer[1] - self.mon.y
            if 0 <= px < width and 0 <= py < height:
                self.draw_hint(cr, width)
        elif local:
            self.draw_selection(cr, local, width, height)
        elif self.sel.pointer:
            px, py = self.sel.pointer[0] - self.mon.x, self.sel.pointer[1] - self.mon.y
            if 0 <= px < width and 0 <= py < height:
                self.draw_crosshair(cr, px, py, width, height)
                self.draw_hint(cr, width)

        if self.sel.pointer:
            px, py = self.sel.pointer[0] - self.mon.x, self.sel.pointer[1] - self.mon.y
            if 0 <= px < width and 0 <= py < height:
                self.draw_loupe(cr, px, py, width, height)

    def draw_selection(self, cr, rect, width, height):
        x, y, w, h = rect
        cr.set_line_width(1)
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(math.floor(x) + 0.5, math.floor(y) + 0.5, round(w), round(h))
        cr.stroke()
        # size label goes on the monitor holding the pointer
        px, py = self.sel.drag_end[0] - self.mon.x, self.sel.drag_end[1] - self.mon.y
        if not (0 <= px < width and 0 <= py < height):
            return
        _, _, iw, ih = self.sel.desktop.to_image(*self.sel.selection())
        lx = min(max(x, 4), width - 120)
        ly = y + h + 8 if y + h + 34 < height else max(y + 8, 4)
        self.draw_pill(cr, f"{iw} × {ih}", lx, ly)

    def draw_crosshair(self, cr, px, py, width, height):
        cr.set_line_width(1)
        cr.set_source_rgba(1, 1, 1, 0.6)
        cr.move_to(0, math.floor(py) + 0.5)
        cr.line_to(width, math.floor(py) + 0.5)
        cr.move_to(math.floor(px) + 0.5, 0)
        cr.line_to(math.floor(px) + 0.5, height)
        cr.set_dash([4, 4])
        cr.stroke()
        cr.set_dash([])

    def draw_hint(self, cr, width):
        text = ("Drag to capture  ·  Click a window or screen  ·  Esc to cancel" if self.sel.windows
                else "Drag to capture  ·  Click for this screen  ·  Esc to cancel")
        self.draw_pill(cr, text,
                       None, 24, center_width=width)

    def draw_pill(self, cr, text, x, y, center_width=None):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 10"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        pad_x, pad_y = 10, 5
        if center_width is not None:
            x = (center_width - tw) / 2 - pad_x
        _rounded(cr, x, y, tw + 2 * pad_x, th + 2 * pad_y, (th + 2 * pad_y) / 2)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.85)
        cr.fill()
        cr.move_to(x + pad_x, y + pad_y)
        cr.set_source_rgb(1, 1, 1)
        PangoCairo.show_layout(cr, layout)

    def draw_loupe(self, cr, px, py, width, height):
        size = LOUPE_SRC * LOUPE_ZOOM
        lx, ly = px + 24, py + 24
        if lx + size > width:
            lx = px - 24 - size
        if ly + size + 28 > height:
            ly = py - 24 - size - 28
        cr.save()
        cr.arc(lx + size / 2, ly + size / 2, size / 2, 0, 2 * math.pi)
        cr.clip()
        # magnified pixels, nearest-neighbour
        cr.save()
        cr.translate(lx, ly)
        cr.scale(LOUPE_ZOOM / self.scale, LOUPE_ZOOM / self.scale)
        src_x = (px - LOUPE_SRC / 2) * self.scale
        src_y = (py - LOUPE_SRC / 2) * self.scale
        cr.set_source_surface(self.surface, -src_x, -src_y)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        cr.restore()
        # centre pixel marker
        c = size / 2 - LOUPE_ZOOM / 2
        cr.set_source_rgba(*ACCENT, 0.9)
        cr.set_line_width(1.5)
        cr.rectangle(lx + c, ly + c, LOUPE_ZOOM, LOUPE_ZOOM)
        cr.stroke()
        cr.restore()
        cr.arc(lx + size / 2, ly + size / 2, size / 2, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(2)
        cr.stroke()
        gx, gy = self.sel.pointer
        ix = round((gx - self.sel.desktop.origin_x) * self.scale)
        iy = round((gy - self.sel.desktop.origin_y) * self.scale)
        self.draw_pill(cr, f"{ix}, {iy}", lx + size / 2 - 40, ly + size + 6)


def _rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
