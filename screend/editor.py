# SPDX-License-Identifier: GPL-3.0-or-later
"""Annotation editor window."""

import copy
import math

import cairo
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import annotations as A
from .imaging import pixbuf_to_surface, png_to_pixbuf, surface_to_png

PALETTE = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#007aff", "#af52de", "#1c1c1e", "#ffffff"]
STROKES = [("Thin", 3), ("Medium", 5), ("Thick", 9)]
HIT_TOL = 6
HANDLE_SIZE = 9
PADDING = 24
MIN_ZOOM, MAX_ZOOM = 0.05, 16

# (id, label, shortcut key, shape class)
TOOLS = [
    ("select", "Select & move", "v", None),
    ("arrow", "Arrow", "a", A.Arrow),
    ("rect", "Rectangle", "r", A.Rect),
    ("ellipse", "Ellipse", "o", A.Ellipse),
    ("line", "Line", "l", A.Line),
    ("pen", "Pen", "p", A.Pen),
    ("highlight", "Highlighter", "h", A.Highlighter),
    ("text", "Text", "t", A.Text),
    ("counter", "Counter", "n", A.Counter),
    ("blur", "Blur", "b", A.Blur),
    ("pixelate", "Pixelate", "x", A.Pixelate),
    ("spotlight", "Spotlight", "s", A.Spotlight),
    ("crop", "Crop", "c", None),
]
TOOL_CLASS = {t[0]: t[3] for t in TOOLS}

# (id, label, colours): one colour = solid, two = diagonal gradient, none = transparent
BACKGROUNDS = [
    ("none", "None", None),
    ("transparent", "Transparent", ()),
    ("sunset", "Sunset", ("#ff7e5f", "#feb47b")),
    ("ocean", "Ocean", ("#2193b0", "#6dd5ed")),
    ("grape", "Grape", ("#8e2de2", "#4a00e0")),
    ("mint", "Mint", ("#43e97b", "#38f9d7")),
    ("peach", "Peach", ("#ffecd2", "#fcb69f")),
    ("dusk", "Dusk", ("#2c3e50", "#4ca1af")),
    ("slate", "Slate", ("#232526", "#414345")),
    ("white", "White", ("#ffffff",)),
    ("light", "Light grey", ("#e9ecf1",)),
    ("black", "Black", ("#111111",)),
]
BACKGROUND_COLORS = {b[0]: b[2] for b in BACKGROUNDS}


def paint_background(cr, colors, x, y, w, h):
    if not colors:
        return
    if len(colors) == 1:
        cr.set_source_rgb(*A.hex_to_rgb(colors[0]))
    else:
        grad = cairo.LinearGradient(x, y, x + w, y + h)
        for i, c in enumerate(colors):
            grad.add_color_stop_rgb(i / (len(colors) - 1), *A.hex_to_rgb(c))
        cr.set_source(grad)
    cr.rectangle(x, y, w, h)
    cr.fill()


_shadow_cache = {}


def soft_shadow(cr, x, y, w, h, radius, blur, alpha):
    """A blurred rounded-rect shadow (blur = downsample, then smooth upscale)."""
    factor = max(2, blur / 2.5)
    key = (round(w), round(h), round(radius), round(blur))
    mask = _shadow_cache.get(key)
    if mask is None:
        mw, mh = math.ceil((w + 4 * blur) / factor), math.ceil((h + 4 * blur) / factor)
        mask = cairo.ImageSurface(cairo.FORMAT_A8, max(1, mw), max(1, mh))
        mc = cairo.Context(mask)
        mc.scale(1 / factor, 1 / factor)
        A.rounded_rect(mc, 2 * blur, 2 * blur, w, h, radius)
        mc.fill()
        if len(_shadow_cache) > 8:
            _shadow_cache.clear()
        _shadow_cache[key] = mask
    cr.save()
    cr.translate(x - 2 * blur, y - 2 * blur)
    cr.scale(factor, factor)
    cr.set_source_rgba(0, 0, 0, alpha)
    pattern = cairo.SurfacePattern(mask)
    pattern.set_filter(cairo.FILTER_BILINEAR)
    cr.mask(pattern)
    cr.restore()


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app, shot):
        super().__init__(application=app, title="Screend")
        self.app = app
        self.shot = shot
        self.config = app.config
        self.base = pixbuf_to_surface(shot.pixbuf)
        self.img_w, self.img_h = self.base.get_width(), self.base.get_height()

        self.shapes = []
        self.crop = (0, 0, self.img_w, self.img_h)
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False

        self.tool = "arrow"
        self.color = self.config.color if self.config.color in PALETTE else PALETTE[0]
        self.stroke_idx = min(max(self.config.stroke, 0), len(STROKES) - 1)
        self.filled = self.config.filled
        self.bg_style = self.config.bg_style if self.config.bg_style in BACKGROUND_COLORS else "none"

        self.active = None      # shape being created
        self.selected = None    # shape selected for move/reshape/recolour
        self.editing = None     # Text shape receiving keystrokes
        self.drag_last = None
        self.drag_origin = None
        self.drag_mode = None   # None | "create" | "move" | "place" | ("handle", index) | "crop"
        self.pending_crop = None
        self.view = (1.0, 0.0, 0.0)  # scale, offset x, offset y
        self.zoom = None  # None = fit to window
        self.pan = [0.0, 0.0]
        self.canvas_size = (1, 1)
        self.pointer = None

        self.build_ui()
        self.set_tool("arrow")
        self.connect("close-request", self.on_close_request)

    # -- UI construction ----------------------------------------------------

    def build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Undo (Ctrl+Z)")
        self.undo_btn.connect("clicked", lambda *_: self.undo())
        self.redo_btn = Gtk.Button(icon_name="edit-redo-symbolic", tooltip_text="Redo (Ctrl+Shift+Z)")
        self.redo_btn.connect("clicked", lambda *_: self.redo())
        header.pack_start(self.undo_btn)
        header.pack_start(self.redo_btn)

        self.title = Adw.WindowTitle(title="Screend")
        header.set_title_widget(self.title)

        copy_btn = Gtk.Button(label="Copy", tooltip_text="Copy to clipboard and close (Ctrl+C)")
        copy_btn.add_css_class("suggested-action")
        copy_btn.connect("clicked", lambda *_: self.copy_and_close())
        header.pack_end(copy_btn)

        menu = Gio.Menu()
        menu.append("Save", "win.save")
        menu.append("Save As…", "win.save-as")
        menu.append("Pin to Screen", "win.pin")
        menu.append("Copy Text (OCR)", "win.copy-text")
        menu.append("Show in Folder", "win.show-folder")
        menu.append("History", "win.history")
        menu.append("Settings", "win.settings")
        menu.append("About Screend", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        save_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Save (Ctrl+S)")
        save_btn.connect("clicked", lambda *_: self.save())
        header.pack_end(save_btn)
        toolbar_view.add_top_bar(header)

        toolbar_view.add_top_bar(self.build_toolbar())

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.add_css_class("screend-canvas")
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)
        pan = Gtk.GestureDrag(button=Gdk.BUTTON_MIDDLE)
        pan.connect("drag-begin", lambda *_: setattr(self, "_pan_start", list(self.pan)))
        pan.connect("drag-update", self.on_pan_update)
        self.area.add_controller(pan)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        self.area.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)
        toolbar_view.set_content(self.area)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        for name, cb in [("save", self.save), ("save-as", self.save_as),
                         ("show-folder", self.show_folder), ("pin", self.pin),
                         ("copy-text", self.copy_text),
                         ("history", self.app.open_history), ("settings", self.app.open_settings),
                         ("about", self.app.open_about)]:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(action)

        self.set_content(toolbar_view)
        self.update_history_buttons()
        self.update_subtitle()
        self.set_default_size(*self.initial_size())

    def build_toolbar(self):
        bar = Gtk.Box(spacing=10, margin_start=8, margin_end=8, margin_top=4, margin_bottom=6,
                      halign=Gtk.Align.CENTER)

        tools = Gtk.Box(css_classes=["linked"])
        self.tool_buttons = {}
        group = None
        for tool_id, label, key, _cls in TOOLS:
            btn = Gtk.ToggleButton(tooltip_text=f"{label} ({key.upper()})", group=group)
            btn.set_child(_ToolIcon(tool_id))
            btn.connect("toggled", self.on_tool_toggled, tool_id)
            group = group or btn
            tools.append(btn)
            self.tool_buttons[tool_id] = btn
        bar.append(tools)

        options = Gtk.Box(css_classes=["linked"])
        self.fill_btn = Gtk.ToggleButton(tooltip_text="Filled shapes / boxed text (F)", active=self.filled)
        self.fill_btn.set_child(_ToolIcon("fill"))
        self.fill_btn.connect("toggled", self.on_fill_toggled)
        options.append(self.fill_btn)
        self.bg_btn = Gtk.MenuButton(tooltip_text="Background (G)", popover=self.build_background_popover())
        self.bg_btn.set_child(_ToolIcon("background"))
        options.append(self.bg_btn)
        bar.append(options)

        colors = Gtk.Box(spacing=3)
        self.color_buttons = {}
        group = None
        for color in PALETTE:
            btn = Gtk.ToggleButton(group=group, tooltip_text=color, css_classes=["screend-swatch", "circular"])
            btn.set_child(_Swatch(color))
            btn.set_active(color == self.color)
            btn.connect("toggled", self.on_color_toggled, color)
            group = group or btn
            colors.append(btn)
            self.color_buttons[color] = btn
        bar.append(colors)

        self.stroke_dd = Gtk.DropDown.new_from_strings([s[0] for s in STROKES])
        self.stroke_dd.set_selected(self.stroke_idx)
        self.stroke_dd.set_tooltip_text("Stroke size")
        self.stroke_dd.connect("notify::selected", self.on_stroke_changed)
        bar.append(self.stroke_dd)
        return bar

    def build_background_popover(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        box.append(Gtk.Label(label="Background", xalign=0, css_classes=["heading"]))

        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        self.bg_buttons = {}
        group = None
        for i, (bg_id, label, colors) in enumerate(BACKGROUNDS):
            btn = Gtk.ToggleButton(group=group, tooltip_text=label, css_classes=["screend-bg-swatch"])
            btn.set_child(_BackgroundSwatch(colors))
            btn.set_active(bg_id == self.bg_style)
            btn.connect("toggled", self.on_background_toggled, bg_id)
            group = group or btn
            grid.attach(btn, i % 6, i // 6, 1, 1)
            self.bg_buttons[bg_id] = btn
        box.append(grid)

        def slider(label, lo, hi, value, setter):
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Label(label=label, xalign=0, width_chars=8))
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
            scale.set_hexpand(True)
            scale.set_value(value)
            scale.set_draw_value(True)
            scale.connect("value-changed", lambda s: setter(int(s.get_value())))
            row.append(scale)
            box.append(row)

        slider("Padding", 0, 200, self.config.bg_padding, lambda v: self.set_bg("bg_padding", v))
        slider("Corners", 0, 48, self.config.bg_radius, lambda v: self.set_bg("bg_radius", v))

        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label="Shadow", xalign=0, hexpand=True))
        shadow = Gtk.Switch(active=self.config.bg_shadow, valign=Gtk.Align.CENTER)
        shadow.connect("notify::active", lambda s, _p: self.set_bg("bg_shadow", s.get_active()))
        row.append(shadow)
        box.append(row)

        popover = Gtk.Popover()
        popover.set_child(box)
        box.set_size_request(300, -1)
        return popover

    def initial_size(self):
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        mw, mh = 1600, 1000
        if monitors.get_n_items():
            g = monitors.get_item(0).get_geometry()
            mw, mh = g.width, g.height
        w = min(max(self.img_w + 2 * PADDING, 1040), int(mw * 0.85))
        h = min(max(self.img_h + 2 * PADDING + 110, 460), int(mh * 0.85))
        return w, h

    # -- tool / style state -------------------------------------------------

    @property
    def stroke(self):
        return STROKES[self.stroke_idx][1]

    def set_tool(self, tool_id):
        self.tool_buttons[tool_id].set_active(True)

    def on_tool_toggled(self, btn, tool_id):
        if not btn.get_active():
            return
        self.finish_text()
        if self.tool == "crop" and tool_id != "crop":
            self.pending_crop = None
        self.tool = tool_id
        if tool_id in ("crop",):
            self.selected = None
        cursor = {"select": "default", "text": "text"}.get(tool_id, "crosshair")
        self.area.set_cursor(Gdk.Cursor.new_from_name(cursor))
        self.update_subtitle()
        self.area.queue_draw()

    def restyle(self, attr, value):
        """Apply a style change to the selected/edited shape, if any."""
        target = self.editing or self.selected
        if target is None or getattr(target, attr) == value:
            return
        if attr == "width" and isinstance(target, A.Highlighter):
            return
        if attr == "filled" and not target.fillable:
            return
        self.push_undo()
        setattr(target, attr, value)
        self.area.queue_draw()

    def on_color_toggled(self, btn, color):
        if btn.get_active():
            self.color = self.config.color = color
            self.restyle("color", color)

    def on_stroke_changed(self, dd, _pspec):
        self.stroke_idx = self.config.stroke = dd.get_selected()
        self.restyle("width", self.stroke)

    def on_fill_toggled(self, btn):
        self.filled = self.config.filled = btn.get_active()
        self.restyle("filled", self.filled)

    def on_background_toggled(self, btn, bg_id):
        if btn.get_active():
            self.set_bg("bg_style", bg_id)

    def set_bg(self, key, value):
        setattr(self.config, key, value)
        if key == "bg_style":
            self.bg_style = value
        self.dirty = True
        self.update_subtitle()
        self.area.queue_draw()

    @property
    def beautify(self):
        return self.bg_style != "none"

    # -- undo ---------------------------------------------------------------

    def snapshot(self):
        return copy.deepcopy(self.shapes), self.crop

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        self.redo_stack.clear()
        self.dirty = True
        self.update_history_buttons()

    def restore(self, state):
        self.shapes, self.crop = copy.deepcopy(state[0]), state[1]
        self.selected = self.editing = self.active = None
        self.update_history_buttons()
        self.update_subtitle()
        self.area.queue_draw()

    def undo(self):
        self.finish_text()
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        self.finish_text()
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())

    def update_history_buttons(self):
        self.undo_btn.set_sensitive(bool(self.undo_stack))
        self.redo_btn.set_sensitive(bool(self.redo_stack))

    def update_subtitle(self):
        w, h = self.output_size()
        text = f"{w} × {h}"
        if self.zoom is not None:
            text += f"  ·  {round(self.zoom * 100)}%"
        self.title.set_subtitle(text)

    # -- coordinates / view -------------------------------------------------

    def padding(self):
        return self.config.bg_padding if self.beautify else 0

    def output_rect(self):
        x, y, w, h = self.crop
        p = self.padding()
        return x - p, y - p, w + 2 * p, h + 2 * p

    def output_size(self):
        _, _, w, h = self.output_rect()
        return round(w), round(h)

    def visible_rect(self):
        if self.tool == "crop":
            return 0, 0, self.img_w, self.img_h
        return self.output_rect()

    def fit_scale(self, width, height):
        _, _, vw, vh = self.visible_rect()
        return max(MIN_ZOOM, min(1.0, (width - 2 * PADDING) / vw, (height - 2 * PADDING) / vh))

    def update_view(self, width, height):
        self.canvas_size = (width, height)
        vx, vy, vw, vh = self.visible_rect()
        scale = self.fit_scale(width, height) if self.zoom is None else self.zoom
        ox = (width - vw * scale) / 2 - vx * scale + self.pan[0]
        oy = (height - vh * scale) / 2 - vy * scale + self.pan[1]
        self.view = (scale, ox, oy)

    def to_image(self, x, y):
        s, ox, oy = self.view
        return (x - ox) / s, (y - oy) / s

    def clamp(self, x, y):
        cx, cy, cw, ch = self.crop if self.tool != "crop" else (0, 0, self.img_w, self.img_h)
        return min(max(x, cx), cx + cw), min(max(y, cy), cy + ch)

    def set_zoom(self, zoom, anchor=None):
        """Zoom keeping the image point under `anchor` (canvas coords) still."""
        width, height = self.canvas_size
        if anchor is None:
            anchor = (width / 2, height / 2)
        ix, iy = self.to_image(*anchor)
        zoom = min(max(zoom, MIN_ZOOM), MAX_ZOOM)
        vx, vy, vw, vh = self.visible_rect()
        centred_x = (width - vw * zoom) / 2 - vx * zoom
        centred_y = (height - vh * zoom) / 2 - vy * zoom
        self.zoom = zoom
        self.pan = [anchor[0] - ix * zoom - centred_x, anchor[1] - iy * zoom - centred_y]
        self.update_subtitle()
        self.area.queue_draw()

    def zoom_fit(self):
        self.zoom = None
        self.pan = [0.0, 0.0]
        self.update_subtitle()
        self.area.queue_draw()

    def on_scroll(self, ctl, dx, dy):
        state = ctl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            current = self.view[0]
            self.set_zoom(current * (1.15 ** -dy), self.pointer)
            return True
        if self.zoom is not None:
            step = 40
            if state & Gdk.ModifierType.SHIFT_MASK:
                dx, dy = dy, dx
            self.pan[0] -= dx * step
            self.pan[1] -= dy * step
            self.area.queue_draw()
            return True
        return False

    def on_pan_update(self, _gesture, dx, dy):
        if self.zoom is None:
            self.zoom = self.view[0]
            self._pan_start = list(self.pan)
            self.update_subtitle()
        self.pan = [self._pan_start[0] + dx, self._pan_start[1] + dy]
        self.area.queue_draw()

    # -- drawing ------------------------------------------------------------

    def render_content(self, cr):
        cr.set_source_surface(self.base, 0, 0)
        cr.paint()
        for shape in self.shapes:
            shape.draw(cr, self.base)
        if self.active and self.active not in self.shapes:
            self.active.draw(cr, self.base)

    def compose(self, cr):
        """The final picture: optional background, shadow and rounded corners around the crop."""
        x, y, w, h = self.crop
        colors = BACKGROUND_COLORS.get(self.bg_style)
        cr.save()
        if colors is None:
            cr.rectangle(x, y, w, h)
            cr.clip()
            self.render_content(cr)
            cr.restore()
            return
        p = self.config.bg_padding
        r = self.config.bg_radius
        paint_background(cr, colors, x - p, y - p, w + 2 * p, h + 2 * p)
        if self.config.bg_shadow:
            blur = max(6, min(40, p * 0.35 + 6))
            soft_shadow(cr, x, y + blur * 0.35, w, h, r, blur, 0.45)
        A.rounded_rect(cr, x, y, w, h, r)
        cr.clip()
        self.render_content(cr)
        cr.restore()

    def draw_decorations(self, cr):
        s = self.view[0]
        shape = self.selected if self.selected in self.shapes else None
        if not shape:
            return
        handles = shape.handles()
        if not handles or self.tool == "select":
            x, y, w, h = shape.bbox()
            cr.set_line_width(1 / s)
            cr.set_dash([4 / s, 3 / s])
            cr.set_source_rgba(0.25, 0.6, 1.0, 0.95)
            cr.rectangle(x - 4 / s, y - 4 / s, w + 8 / s, h + 8 / s)
            cr.stroke()
            cr.set_dash([])
        size = HANDLE_SIZE / s
        for hx, hy in handles:
            cr.rectangle(hx - size / 2, hy - size / 2, size, size)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgb(0.25, 0.6, 1.0)
            cr.set_line_width(1.5 / s)
            cr.stroke()

    def draw(self, _area, cr, width, height):
        self.update_view(width, height)
        s, ox, oy = self.view
        cr.save()
        cr.translate(ox, oy)
        cr.scale(s, s)

        if self.tool == "crop":
            cr.save()
            cr.rectangle(0, 0, self.img_w, self.img_h)
            cr.clip()
            self.render_content(cr)
            cr.restore()
            self.draw_crop_overlay(cr, s)
        else:
            if not self.beautify:
                x, y, w, h = self.crop
                cr.rectangle(x + 2 / s, y + 4 / s, w, h)
                cr.set_source_rgba(0, 0, 0, 0.35)
                cr.fill()
            if s < 1:
                # smooth downscaling
                cr.push_group()
                self.compose(cr)
                cr.pop_group_to_source()
                cr.get_source().set_filter(cairo.FILTER_GOOD)
                cr.paint()
            else:
                self.compose(cr)
            self.draw_decorations(cr)
        cr.restore()

    def draw_crop_overlay(self, cr, s):
        crop = self.pending_crop or self.crop
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, self.img_w, self.img_h)
        cr.rectangle(*crop)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.set_line_width(2 / s)
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(*crop)
        cr.stroke()
        x, y, w, h = crop
        cr.set_line_width(1 / s)
        cr.set_source_rgba(1, 1, 1, 0.4)
        for i in (1, 2):
            cr.move_to(x + w * i / 3, y)
            cr.line_to(x + w * i / 3, y + h)
            cr.move_to(x, y + h * i / 3)
            cr.line_to(x + w, y + h * i / 3)
        cr.stroke()

    def export_png(self):
        x, y, w, h = self.output_rect()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, round(w), round(h))
        cr = cairo.Context(surface)
        cr.translate(-round(x), -round(y))
        selected, self.selected = self.selected, None
        self.compose(cr)
        self.selected = selected
        surface.flush()
        return surface_to_png(surface)

    # -- pointer ------------------------------------------------------------

    def shape_at(self, x, y):
        tol = HIT_TOL / self.view[0]
        for shape in reversed(self.shapes):
            if shape.hit(x, y, tol):
                return shape
        return None

    def handle_at(self, x, y):
        if self.selected not in self.shapes:
            return None
        tol = HANDLE_SIZE / self.view[0]
        for i, (hx, hy) in enumerate(self.selected.handles()):
            if abs(x - hx) <= tol and abs(y - hy) <= tol:
                return i
        return None

    def constrained(self, gesture):
        return bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)

    def on_motion(self, _ctl, x, y):
        self.pointer = (x, y)
        if self.tool == "crop":
            return
        ix, iy = self.to_image(x, y)
        if self.handle_at(ix, iy) is not None:
            name = "move"
        elif self.tool == "select":
            name = "move" if self.shape_at(ix, iy) else "default"
        elif self.selected and self.selected in self.shapes and \
                self.selected.hit(ix, iy, HIT_TOL / self.view[0]) and self.tool != "text":
            name = "move"
        else:
            name = {"text": "text"}.get(self.tool, "crosshair")
        self.area.set_cursor(Gdk.Cursor.new_from_name(name))

    def on_drag_begin(self, gesture, x, y):
        self.area.grab_focus()
        ix, iy = self.to_image(x, y)
        self.drag_origin = self.drag_last = (ix, iy)
        self.drag_mode = None

        if self.tool == "crop":
            self.drag_mode = "crop"
            self.pending_crop = None
            return

        handle = self.handle_at(ix, iy)
        if handle is not None:
            self.finish_text()
            self.push_undo()
            self.drag_mode = ("handle", handle)
            return

        if self.tool == "text":
            return  # handled on release so a click can pick existing text

        if self.tool == "select":
            self.selected = self.shape_at(ix, iy)
            if self.selected:
                self.push_undo()
                self.drag_mode = "move"
            self.area.queue_draw()
            return

        # with any tool, dragging the selected shape moves it
        if self.selected in self.shapes and self.selected.hit(ix, iy, HIT_TOL / self.view[0]):
            self.finish_text()
            self.push_undo()
            self.drag_mode = "move"
            return

        self.finish_text()
        self.selected = None
        ix, iy = self.clamp(ix, iy)
        cls = TOOL_CLASS[self.tool]
        if cls is A.Counter:
            number = 1 + max((s.number for s in self.shapes if isinstance(s, A.Counter)), default=0)
            self.push_undo()
            shape = A.Counter(self.color, self.stroke, ix, iy, number)
            self.shapes.append(shape)
            self.selected = shape
            self.drag_mode = "place"  # dragging positions the fresh counter
        else:
            self.active = cls(self.color, self.stroke, ix, iy, filled=self.filled)
            self.drag_mode = "create"
        self.area.queue_draw()

    def on_drag_update(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        ix, iy = self.to_image(sx + dx, sy + dy)
        mode = self.drag_mode

        if mode == "crop":
            ox, oy = self.clamp(*self.drag_origin)
            ex, ey = self.clamp(ix, iy)
            p1 = A.snap_square((ox, oy), (ex, ey)) if self.constrained(gesture) else (ex, ey)
            self.pending_crop = tuple(round(v) for v in A._norm((ox, oy), p1))
        elif isinstance(mode, tuple):
            self.selected.drag_handle(mode[1], *self.clamp(ix, iy), constrain=self.constrained(gesture))
        elif mode in ("move", "place") and self.selected:
            lx, ly = self.drag_last
            self.selected.move(ix - lx, iy - ly)
        elif mode == "create" and self.active:
            self.active.drag_to(*self.clamp(ix, iy), constrain=self.constrained(gesture))
        self.drag_last = (ix, iy)
        self.area.queue_draw()

    def on_drag_end(self, gesture, dx, dy):
        click = math.hypot(dx, dy) < 4
        mode = self.drag_mode
        self.drag_mode = None

        if self.tool == "text" and mode is None:
            self.on_text_click(*self.drag_origin)
        elif mode == "crop":
            if self.pending_crop and self.pending_crop[2] > 4 and self.pending_crop[3] > 4:
                self.push_undo()
                self.crop = self.pending_crop
                self.update_subtitle()
            self.pending_crop = None
        elif mode == "move" and click and self.undo_stack:
            # nothing actually moved
            self.undo_stack.pop()
            self.update_history_buttons()
        elif mode == "create" and self.active:
            if not self.active.is_empty():
                self.push_undo()
                self.shapes.append(self.active)
                if self.active.handles():
                    self.selected = self.active
            self.active = None
        self.area.queue_draw()

    # -- text ---------------------------------------------------------------

    def on_text_click(self, ix, iy):
        hit = self.shape_at(ix, iy)
        self.finish_text()
        self.push_undo()
        if isinstance(hit, A.Text):
            shape = hit
        else:
            fx, fy = self.clamp(ix, iy)
            shape = A.Text(self.color, self.stroke, fx, fy - 0.6 * (14 + self.stroke * 3), filled=self.filled)
            self.shapes.append(shape)
        shape.editing = True
        self.editing = shape
        self.selected = None
        self.area.queue_draw()

    def finish_text(self):
        shape = self.editing
        if not shape:
            return
        shape.editing = False
        self.editing = None
        if shape.is_empty() and shape in self.shapes:
            self.shapes.remove(shape)
        self.area.queue_draw()

    def on_text_key(self, keyval, state):
        shape = self.editing
        if keyval == Gdk.KEY_Escape:
            self.finish_text()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                shape.text += "\n"
            else:
                self.finish_text()
        elif keyval == Gdk.KEY_BackSpace:
            shape.text = shape.text[:-1]
        elif state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_v:
            self.paste_into_text(shape)
        elif state & Gdk.ModifierType.CONTROL_MASK:
            return False
        else:
            ch = chr(Gdk.keyval_to_unicode(keyval))
            if ch == "\0" or not ch.isprintable():
                return True
            shape.text += ch
        self.area.queue_draw()
        return True

    def paste_into_text(self, shape):
        def done(clip, res):
            try:
                text = clip.read_text_finish(res)
            except GLib.Error:
                return
            if text and shape is self.editing:
                shape.text += text
                self.area.queue_draw()
        self.get_clipboard().read_text_async(None, done)

    # -- keyboard -----------------------------------------------------------

    def on_key(self, _ctl, keyval, _code, state):
        if self.editing:
            return self.on_text_key(keyval, state)
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        lower = Gdk.keyval_to_lower(keyval)
        if ctrl:
            if lower == Gdk.KEY_z:
                self.redo() if shift else self.undo()
            elif lower == Gdk.KEY_y:
                self.redo()
            elif lower == Gdk.KEY_c:
                self.copy_and_close()
            elif lower == Gdk.KEY_s:
                self.save_as() if shift else self.save()
            elif lower == Gdk.KEY_w:
                self.close()
            elif lower == Gdk.KEY_0:
                self.zoom_fit()
            elif lower == Gdk.KEY_1:
                self.set_zoom(1.0)
            elif lower in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.set_zoom(self.view[0] * 1.25)
            elif lower in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.set_zoom(self.view[0] / 1.25)
            else:
                return False
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected in self.shapes:
            self.push_undo()
            self.shapes.remove(self.selected)
            self.selected = None
            self.area.queue_draw()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down) and self.selected in self.shapes:
            step = 10 if shift else 1
            dx = {Gdk.KEY_Left: -step, Gdk.KEY_Right: step}.get(keyval, 0)
            dy = {Gdk.KEY_Up: -step, Gdk.KEY_Down: step}.get(keyval, 0)
            self.push_undo()
            self.selected.move(dx, dy)
            self.area.queue_draw()
            return True
        if keyval == Gdk.KEY_Escape:
            if self.selected:
                self.selected = None
                self.area.queue_draw()
            else:
                self.close()
            return True
        if lower == Gdk.KEY_f:
            self.fill_btn.set_active(not self.fill_btn.get_active())
            return True
        if lower == Gdk.KEY_g:
            self.bg_btn.popup()
            return True
        for tool_id, _label, key, _cls in TOOLS:
            if lower == Gdk.keyval_from_name(key):
                self.set_tool(tool_id)
                return True
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_8:
            self.color_buttons[PALETTE[keyval - Gdk.KEY_1]].set_active(True)
            return True
        return False

    # -- output -------------------------------------------------------------

    def commit(self):
        """Write annotations back into the shot (file + quick access thumbnail)."""
        self.finish_text()
        self.config.save()
        if self.dirty:
            self.shot.replace(self.export_png())
            self.dirty = False

    def copy_and_close(self):
        self.commit()
        self.shot.copy(self.get_display())
        self.app.toast("Copied to clipboard")
        self.destroy()

    def save(self):
        self.commit()
        path = self.shot.save()
        self.app.toast(f"Saved to {path.name}")

    def save_as(self):
        self.commit()
        dialog = Gtk.FileDialog(title="Save Screenshot", initial_name=self.shot.default_path().name)
        folder = self.config.save_path
        if folder.exists():
            dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))

        def done(dlg, res):
            try:
                gfile = dlg.save_finish(res)
            except GLib.Error:
                return
            path = self.shot.save_as(gfile.get_path())
            self.app.toast(f"Saved to {path.name}")
        dialog.save(self, None, done)

    def pin(self):
        self.commit()
        self.app.pin(self.shot)

    def copy_text(self):
        self.finish_text()
        self.app.recognize_and_copy(png_to_pixbuf(self.export_png()), display=self.get_display())

    def show_folder(self):
        path = self.shot.path or self.config.save_path
        self.app.show_in_folder(path)

    def on_close_request(self, _win):
        if not self.dirty:
            self.config.save()
            return False
        dialog = Adw.AlertDialog(heading="Keep your annotations?",
                                 body="The changes haven't been applied to the screenshot yet.")
        dialog.add_response("discard", "Discard")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("apply")
        dialog.set_close_response("cancel")

        def on_response(_dlg, response):
            if response == "apply":
                self.commit()
            if response in ("apply", "discard"):
                self.dirty = False
                self.destroy()
        dialog.connect("response", on_response)
        dialog.present(self)
        return True


class _Swatch(Gtk.DrawingArea):
    def __init__(self, color):
        super().__init__(content_width=16, content_height=16)
        rgb = A.hex_to_rgb(color)

        def draw(_a, cr, w, h):
            cr.arc(w / 2, h / 2, min(w, h) / 2 - 0.5, 0, 2 * math.pi)
            cr.set_source_rgb(*rgb)
            cr.fill_preserve()
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            cr.set_line_width(1)
            cr.stroke()
        self.set_draw_func(draw)


class _BackgroundSwatch(Gtk.DrawingArea):
    def __init__(self, colors):
        super().__init__(content_width=32, content_height=24)
        self.colors = colors
        self.set_draw_func(self.draw)

    def draw(self, _a, cr, w, h):
        A.rounded_rect(cr, 0.5, 0.5, w - 1, h - 1, 5)
        cr.clip_preserve()
        if self.colors:
            paint_background(cr, self.colors, 0, 0, w, h)
        else:
            # checkerboard for transparent, a slash for none
            for row in range(0, h, 6):
                for col in range(0, w, 6):
                    shade = 0.85 if (row + col) // 6 % 2 else 0.6
                    cr.set_source_rgb(shade, shade, shade)
                    cr.rectangle(col, row, 6, 6)
                    cr.fill()
            if self.colors is None:
                cr.set_source_rgb(0.85, 0.2, 0.2)
                cr.set_line_width(2)
                cr.move_to(w - 2, 2)
                cr.line_to(2, h - 2)
                cr.stroke()
        A.rounded_rect(cr, 0.5, 0.5, w - 1, h - 1, 5)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
        cr.set_line_width(1)
        cr.stroke()


class _ToolIcon(Gtk.DrawingArea):
    """Tool glyphs drawn with cairo in the button's foreground colour."""

    def __init__(self, tool_id):
        super().__init__(content_width=18, content_height=18)
        self.tool_id = tool_id
        self.set_draw_func(self.draw)

    def draw(self, _a, cr, w, h):
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
        cr.set_line_width(1.6)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        getattr(self, "draw_" + self.tool_id)(cr)

    def draw_select(self, cr):
        cr.move_to(4, 2)
        cr.line_to(4, 15)
        cr.line_to(7.5, 11.5)
        cr.line_to(10, 16.5)
        cr.line_to(12, 15.5)
        cr.line_to(9.5, 10.5)
        cr.line_to(14, 10.5)
        cr.close_path()
        cr.stroke()

    def draw_arrow(self, cr):
        cr.move_to(3, 15)
        cr.line_to(14, 4)
        cr.stroke()
        cr.move_to(15.5, 2.5)
        cr.line_to(8, 4.5)
        cr.line_to(13.5, 10)
        cr.close_path()
        cr.fill()

    def draw_rect(self, cr):
        cr.rectangle(2.5, 4, 13, 10)
        cr.stroke()

    def draw_ellipse(self, cr):
        cr.save()
        cr.translate(9, 9)
        cr.scale(7, 5.5)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.stroke()

    def draw_line(self, cr):
        cr.move_to(3, 15)
        cr.line_to(15, 3)
        cr.stroke()

    def draw_pen(self, cr):
        cr.move_to(2.5, 13)
        cr.curve_to(5, 5, 8, 16, 10.5, 9)
        cr.curve_to(12, 5, 14, 6, 15.5, 4.5)
        cr.stroke()

    def draw_highlight(self, cr):
        cr.move_to(11, 2.5)
        cr.line_to(15.5, 7)
        cr.line_to(9, 13.5)
        cr.line_to(4.5, 9)
        cr.close_path()
        cr.stroke()
        cr.move_to(4.5, 9)
        cr.line_to(2.5, 14)
        cr.line_to(4, 15.5)
        cr.line_to(9, 13.5)
        cr.stroke()

    def draw_text(self, cr):
        cr.set_line_width(2)
        cr.move_to(3.5, 4)
        cr.line_to(14.5, 4)
        cr.move_to(9, 4)
        cr.line_to(9, 15)
        cr.stroke()

    def draw_counter(self, cr):
        cr.arc(9, 9, 7, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(7.5, 6.5)
        cr.line_to(9.5, 5)
        cr.line_to(9.5, 13)
        cr.stroke()

    def draw_blur(self, cr):
        src = cr.get_source()
        for i, r in enumerate((7, 5, 3)):
            cr.arc(9, 9, r, 0, 2 * math.pi)
            cr.save()
            cr.set_source(src)
            cr.clip()
            cr.paint_with_alpha(0.3 + i * 0.3)
            cr.restore()

    def draw_pixelate(self, cr):
        for row in range(3):
            for col in range(3):
                if (row + col) % 2 == 0:
                    cr.rectangle(3 + col * 4, 3 + row * 4, 4, 4)
        cr.fill()
        cr.set_line_width(1)
        cr.rectangle(2.5, 2.5, 13, 13)
        cr.stroke()

    def draw_spotlight(self, cr):
        cr.rectangle(2, 2, 14, 14)
        cr.rectangle(5.5, 5.5, 7, 7)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()

    def draw_crop(self, cr):
        cr.move_to(5, 1.5)
        cr.line_to(5, 13)
        cr.line_to(16.5, 13)
        cr.move_to(1.5, 5)
        cr.line_to(13, 5)
        cr.line_to(13, 16.5)
        cr.stroke()

    def draw_fill(self, cr):
        A.rounded_rect(cr, 3, 4, 12, 10, 2)
        cr.fill()

    def draw_background(self, cr):
        # a picture on a backdrop
        src = cr.get_source()
        cr.rectangle(1.5, 2.5, 15, 13)
        cr.save()
        cr.set_source(src)
        cr.clip()
        cr.paint_with_alpha(0.35)
        cr.restore()
        A.rounded_rect(cr, 5, 6, 8, 6, 1.5)
        cr.fill()
