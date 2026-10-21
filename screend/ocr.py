# SPDX-License-Identifier: GPL-3.0-or-later
"""Text recognition with Tesseract (optional: `sudo apt install tesseract-ocr`)."""

import shutil

from gi.repository import GdkPixbuf, Gio, GLib

from .imaging import pixbuf_to_png

INSTALL_HINT = "Text recognition needs Tesseract: sudo apt install tesseract-ocr"
# Tesseract reads best when text is ~30px tall; screenshots of UI text are often half that
MIN_HEIGHT_FOR_NATIVE = 1200


def available():
    return shutil.which("tesseract") is not None


def recognize(pixbuf, language, callback):
    """callback(text, None) or callback(None, error_message). Runs in the background."""
    if not available():
        callback(None, INSTALL_HINT)
        return
    if pixbuf.get_height() < MIN_HEIGHT_FOR_NATIVE:
        pixbuf = pixbuf.scale_simple(pixbuf.get_width() * 2, pixbuf.get_height() * 2,
                                     GdkPixbuf.InterpType.BILINEAR)
    png = pixbuf_to_png(pixbuf)
    try:
        proc = Gio.Subprocess.new(
            ["tesseract", "stdin", "stdout", "-l", language or "eng", "--psm", "3"],
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
    except GLib.Error as e:
        callback(None, e.message)
        return

    def done(p, res):
        try:
            _ok, out, err = p.communicate_finish(res)
        except GLib.Error as e:
            callback(None, e.message)
            return
        if p.get_exit_status() != 0:
            lines = err.get_data().decode(errors="replace").strip().splitlines() if err else []
            callback(None, lines[-1] if lines else "Tesseract failed")
            return
        callback(clean(out.get_data().decode(errors="replace") if out else ""), None)
    proc.communicate_async(GLib.Bytes.new(png), None, done)


def clean(text):
    """Trim Tesseract's trailing form feed and runs of blank lines."""
    lines = [line.rstrip() for line in text.replace("\f", "").splitlines()]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
