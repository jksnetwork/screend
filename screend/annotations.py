# SPDX-License-Identifier: GPL-3.0-or-later
"""Annotation shapes. All coordinates are in image pixels."""

import math

import cairo
from gi.repository import Pango, PangoCairo

SHADOW = (0, 0, 0, 0.35)
SHADOW_OFFSET = 1.5

_measure_ctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))


def hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def is_light(color):
    return sum(hex_to_rgb(color)) > 2.2


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _norm(p0, p1):
    (x0, y0), (x1, y1) = p0, p1
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def snap_angle(p0, p1):
    """Constrain p1 so p0->p1 lies on a 45 degree increment."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    return p0[0] + length * math.cos(angle), p0[1] + length * math.sin(angle)


def snap_square(p0, p1):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    side = max(abs(dx), abs(dy))
    return p0[0] + math.copysign(side, dx or 1), p0[1] + math.copysign(side, dy or 1)


def rounded_rect(cr, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    if r == 0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Shape:
    shadow = True
    # how the "constrain" modifier (Shift) applies while dragging
    constrain = None
    # whether the "fill" option means anything for this shape
    fillable = False

    def __init__(self, color, width, filled=False):
        self.color = color
        self.width = width
        self.filled = filled and self.fillable

    def draw(self, cr, base):
        if self.shadow:
            cr.save()
            cr.translate(SHADOW_OFFSET, SHADOW_OFFSET)
            self.paint(cr, SHADOW, base)
            cr.restore()
        self.paint(cr, (*hex_to_rgb(self.color), 1.0), base)

    def paint(self, cr, rgba, base):
        raise NotImplementedError

    def bbox(self):
        raise NotImplementedError

    def hit(self, x, y, tol):
        bx, by, bw, bh = self.bbox()
        return bx - tol <= x <= bx + bw + tol and by - tol <= y <= by + bh + tol

    def move(self, dx, dy):
        raise NotImplementedError

    def drag_to(self, x, y, constrain=False):
        """Update while the shape is being created."""

    def handles(self):
        """Points the user can drag to reshape the shape."""
        return []

    def drag_handle(self, index, x, y, constrain=False):
        pass

    def is_empty(self):
        return False


class _TwoPoint(Shape):
    def __init__(self, color, width, x, y, filled=False):
        super().__init__(color, width, filled)
        self.p0 = (x, y)
        self.p1 = (x, y)

    def drag_to(self, x, y, constrain=False):
        self.p1 = (x, y)
        if constrain and self.constrain:
            self.p1 = self.constrain(self.p0, self.p1)

    def move(self, dx, dy):
        self.p0 = (self.p0[0] + dx, self.p0[1] + dy)
        self.p1 = (self.p1[0] + dx, self.p1[1] + dy)

    def bbox(self):
        return _norm(self.p0, self.p1)

    def is_empty(self):
        return math.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]) < 3

    def handles(self):
        return [self.p0, self.p1]

    def drag_handle(self, index, x, y, constrain=False):
        if index == 0:
            self.p0 = self.constrain(self.p1, (x, y)) if constrain and self.constrain else (x, y)
        else:
            self.p1 = self.constrain(self.p0, (x, y)) if constrain and self.constrain else (x, y)


class _Boxed(_TwoPoint):
    """Rectangle-like shapes: reshaped by their four corners."""

    constrain = staticmethod(snap_square)

    def handles(self):
        x, y, w, h = self.bbox()
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]

    def drag_handle(self, index, x, y, constrain=False):
        corners = self.handles()
        anchor = corners[(index + 2) % 4]
        point = snap_square(anchor, (x, y)) if constrain else (x, y)
        self.p0, self.p1 = anchor, point


class Arrow(_TwoPoint):
    constrain = staticmethod(snap_angle)

    def paint(self, cr, rgba, base):
        (x0, y0), (x1, y1) = self.p0, self.p1
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1:
            return
        w = self.width
        head = min(max(w * 3.5, 12), length * 0.6)
        half = head * 0.6
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        nx, ny = -uy, ux
        bx, by = x1 - ux * head, y1 - uy * head  # base of the head
        tail = w * 0.25
        shaft = w * 0.55
        cr.move_to(x0 + nx * tail, y0 + ny * tail)
        cr.line_to(bx + nx * shaft, by + ny * shaft)
        cr.line_to(bx + nx * half, by + ny * half)
        cr.line_to(x1, y1)
        cr.line_to(bx - nx * half, by - ny * half)
        cr.line_to(bx - nx * shaft, by - ny * shaft)
        cr.line_to(x0 - nx * tail, y0 - ny * tail)
        cr.close_path()
        cr.set_source_rgba(*rgba)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(1)
        cr.fill_preserve()
        cr.stroke()

    def hit(self, x, y, tol):
        return _seg_dist(x, y, *self.p0, *self.p1) <= tol + self.width


class Line(_TwoPoint):
    constrain = staticmethod(snap_angle)

    def paint(self, cr, rgba, base):
        cr.move_to(*self.p0)
        cr.line_to(*self.p1)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(self.width)
        cr.set_source_rgba(*rgba)
        cr.stroke()

    def hit(self, x, y, tol):
        return _seg_dist(x, y, *self.p0, *self.p1) <= tol + self.width


class Rect(_Boxed):
    fillable = True

    def paint(self, cr, rgba, base):
        x, y, w, h = self.bbox()
        rounded_rect(cr, x, y, w, h, min(self.width * 1.2, w / 2, h / 2))
        cr.set_source_rgba(*rgba)
        if self.filled:
            cr.fill()
            return
        cr.set_line_width(self.width)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()


class Ellipse(_Boxed):
    fillable = True

    def paint(self, cr, rgba, base):
        x, y, w, h = self.bbox()
        if w < 1 or h < 1:
            return
        cr.save()
        cr.translate(x + w / 2, y + h / 2)
        cr.scale(w / 2, h / 2)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.set_source_rgba(*rgba)
        if self.filled:
            cr.fill()
            return
        cr.set_line_width(self.width)
        cr.stroke()


def _resample(cr, base, rect, factor, up_filter):
    """Draw base[rect] shrunk by `factor` and stretched back: pixelate or blur."""
    x, y, w, h = rect
    sw, sh = max(1, math.ceil(w / factor)), max(1, math.ceil(h / factor))
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
    sc = cairo.Context(small)
    sc.scale(sw / w, sh / h)
    sc.set_source_surface(base, -x, -y)
    sc.get_source().set_filter(cairo.FILTER_GOOD)
    sc.get_source().set_extend(cairo.EXTEND_PAD)
    sc.paint()
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.translate(x, y)
    cr.scale(w / sw, h / sh)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(up_filter)
    cr.get_source().set_extend(cairo.EXTEND_PAD)
    cr.paint()
    cr.restore()


class Pixelate(_Boxed):
    shadow = False

    def paint(self, cr, rgba, base):
        rect = tuple(round(v) for v in self.bbox())
        if rect[2] >= 1 and rect[3] >= 1:
            _resample(cr, base, rect, max(12, self.width * 3), cairo.FILTER_NEAREST)


def _halve(surface, w, h):
    sw, sh = max(1, math.ceil(w / 2)), max(1, math.ceil(h / 2))
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
    c = cairo.Context(out)
    c.scale(sw / w, sh / h)
    c.set_source_surface(surface, 0, 0)
    c.get_source().set_filter(cairo.FILTER_GOOD)
    c.get_source().set_extend(cairo.EXTEND_PAD)
    c.paint()
    return out


def _double(surface, w, h):
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    c = cairo.Context(out)
    c.scale(w / surface.get_width(), h / surface.get_height())
    c.set_source_surface(surface, 0, 0)
    c.get_source().set_filter(cairo.FILTER_BILINEAR)
    c.get_source().set_extend(cairo.EXTEND_PAD)
    c.paint()
    return out


class Blur(_Boxed):
    shadow = False

    def paint(self, cr, rgba, base):
        x, y, w, h = (round(v) for v in self.bbox())
        if w < 1 or h < 1:
            return
        # repeated halving then doubling approximates a gaussian without seams
        margin = 16
        src = cairo.ImageSurface(cairo.FORMAT_ARGB32, w + 2 * margin, h + 2 * margin)
        c = cairo.Context(src)
        c.set_source_surface(base, -(x - margin), -(y - margin))
        c.get_source().set_extend(cairo.EXTEND_PAD)
        c.paint()
        sizes = [(src.get_width(), src.get_height())]
        levels = 3 + self.width // 3
        surf = src
        for _ in range(levels):
            surf = _halve(surf, *sizes[-1])
            sizes.append((surf.get_width(), surf.get_height()))
        for size in reversed(sizes[:-1]):
            surf = _double(surf, *size)
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.set_source_surface(surf, x - margin, y - margin)
        cr.paint()
        cr.restore()


class Spotlight(_Boxed):
    """Dims everything except the chosen rectangle."""

    shadow = False

    def paint(self, cr, rgba, base):
        x, y, w, h = self.bbox()
        cr.save()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(-1e5, -1e5, 2e5, 2e5)
        rounded_rect(cr, x, y, w, h, min(8, w / 2, h / 2))
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.fill()
        cr.restore()


class _Stroke(Shape):
    def __init__(self, color, width, x, y, filled=False):
        super().__init__(color, width)
        self.points = [(x, y)]

    def drag_to(self, x, y, constrain=False):
        lx, ly = self.points[-1]
        if math.hypot(x - lx, y - ly) >= 1.5:
            self.points.append((x, y))

    def move(self, dx, dy):
        self.points = [(x + dx, y + dy) for x, y in self.points]

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        pad = self.width / 2
        return min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad

    def hit(self, x, y, tol):
        pts = self.points
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1]) <= tol + self.width
        return any(_seg_dist(x, y, *a, *b) <= tol + self.width / 2 for a, b in zip(pts, pts[1:]))

    def is_empty(self):
        return len(self.points) < 2

    def path(self, cr):
        pts = self.points
        cr.move_to(*pts[0])
        if len(pts) < 3:
            for p in pts[1:]:
                cr.line_to(*p)
            return
        # smooth through midpoints
        for (ax, ay), (bx, by) in zip(pts[1:-1], pts[2:]):
            cr.curve_to(ax, ay, ax, ay, (ax + bx) / 2, (ay + by) / 2)
        cr.line_to(*pts[-1])


class Pen(_Stroke):
    def paint(self, cr, rgba, base):
        self.path(cr)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(self.width)
        cr.set_source_rgba(*rgba)
        cr.stroke()


class Highlighter(_Stroke):
    shadow = False

    def __init__(self, color, width, x, y, filled=False):
        super().__init__(color, width * 4, x, y)

    def drag_to(self, x, y, constrain=False):
        if constrain and len(self.points) >= 1:
            # Shift keeps the marker on a horizontal line
            self.points = [self.points[0], (x, self.points[0][1])]
        else:
            super().drag_to(x, y)

    def paint(self, cr, rgba, base):
        self.path(cr)
        cr.set_line_cap(cairo.LINE_CAP_SQUARE)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(self.width)
        cr.set_source_rgba(rgba[0], rgba[1], rgba[2], 0.45)
        cr.stroke()


class Counter(Shape):
    def __init__(self, color, width, x, y, number):
        super().__init__(color, width)
        self.x, self.y = x, y
        self.number = number

    @property
    def radius(self):
        return 10 + self.width * 2

    def paint(self, cr, rgba, base):
        r = self.radius
        cr.arc(self.x, self.y, r, 0, 2 * math.pi)
        cr.set_source_rgba(*rgba)
        cr.fill()
        if rgba == SHADOW:
            return
        cr.arc(self.x, self.y, r, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(max(1.5, r / 9))
        cr.stroke()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {r * 0.95:.1f}px"))
        layout.set_text(str(self.number), -1)
        _, logical = layout.get_pixel_extents()
        cr.move_to(self.x - logical.width / 2, self.y - logical.height / 2)
        cr.set_source_rgb(*((0.1, 0.1, 0.1) if is_light(self.color) else (1, 1, 1)))
        PangoCairo.show_layout(cr, layout)

    def bbox(self):
        r = self.radius
        return self.x - r, self.y - r, 2 * r, 2 * r

    def move(self, dx, dy):
        self.x += dx
        self.y += dy


class Text(Shape):
    fillable = True  # "filled" text sits on a solid rounded box

    def __init__(self, color, width, x, y, text="", filled=False):
        super().__init__(color, width, filled)
        self.x, self.y = x, y
        self.text = text
        self.editing = False

    @property
    def font_px(self):
        return 14 + self.width * 3

    @property
    def box_pad(self):
        return round(self.font_px * 0.35)

    def _layout(self, cr):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {self.font_px}px"))
        layout.set_text(self.text or " ", -1)
        return layout

    def paint(self, cr, rgba, base):
        layout = self._layout(cr)
        if self.filled:
            tw, th = layout.get_pixel_size()
            p = self.box_pad
            rounded_rect(cr, self.x - p, self.y - p * 0.6, max(tw, 4) + 2 * p, th + 1.2 * p, p)
            cr.set_source_rgba(*rgba)
            cr.fill()
            if rgba == SHADOW:
                return
            cr.move_to(self.x, self.y)
            cr.set_source_rgb(*((0.08, 0.08, 0.08) if is_light(self.color) else (1, 1, 1)))
            PangoCairo.show_layout(cr, layout)
            return
        cr.move_to(self.x, self.y)
        if rgba != SHADOW:
            # contrasting outline keeps text readable on any background
            PangoCairo.layout_path(cr, layout)
            cr.set_source_rgba(*((0, 0, 0, 0.6) if is_light(self.color) else (1, 1, 1, 0.9)))
            cr.set_line_width(max(2, self.font_px / 7))
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke()
            cr.move_to(self.x, self.y)
        cr.set_source_rgba(*rgba)
        PangoCairo.show_layout(cr, layout)

    def draw(self, cr, base):
        super().draw(cr, base)
        if self.editing:
            layout = self._layout(_measure_ctx)
            _, _, _, h = self._text_extents()
            line_count = layout.get_line_count()
            line_h = h / max(1, line_count)
            last = layout.get_line_readonly(line_count - 1)
            _, ext = last.get_pixel_extents()
            caret_x = self.x + (ext.width if self.text and not self.text.endswith("\n") else 0)
            if self.filled:
                cr.set_source_rgb(*((0.08, 0.08, 0.08) if is_light(self.color) else (1, 1, 1)))
            else:
                cr.set_source_rgba(*hex_to_rgb(self.color), 1)
            cr.set_line_width(2)
            cr.move_to(caret_x + 1, self.y + h - line_h + 2)
            cr.line_to(caret_x + 1, self.y + h - 2)
            cr.stroke()

    def _text_extents(self):
        w, h = self._layout(_measure_ctx).get_pixel_size()
        return self.x, self.y, max(w, 4), h

    def bbox(self):
        x, y, w, h = self._text_extents()
        if self.filled:
            p = self.box_pad
            return x - p, y - p * 0.6, w + 2 * p, h + 1.2 * p
        return x, y, w, h

    def move(self, dx, dy):
        self.x += dx
        self.y += dy

    def is_empty(self):
        return not self.text.strip()
