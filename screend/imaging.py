# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversions between GdkPixbuf, cairo surfaces, PNG bytes and Gdk textures."""

import io

import cairo
from gi.repository import Gdk, GdkPixbuf, GLib


def pixbuf_to_surface(pixbuf):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.paint()
    surface.flush()
    return surface


def surface_to_png(surface) -> bytes:
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()


def pixbuf_to_png(pixbuf) -> bytes:
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    return bytes(data)


def png_to_texture(png: bytes):
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))


def png_to_pixbuf(png: bytes):
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png)
    loader.close()
    return loader.get_pixbuf()


def cover_thumbnail(pixbuf, width, height):
    """Scale and centre-crop to exactly width x height (like CSS object-fit: cover)."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    fit = max(width / w, height / h)
    tw, th = max(width, round(w * fit)), max(height, round(h * fit))
    scaled = pixbuf.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
    return scaled.new_subpixbuf((tw - width) // 2, (th - height) // 2, width, height)
