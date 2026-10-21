# SPDX-License-Identifier: GPL-3.0-or-later
"""Video helpers built on ffmpeg (thumbnails, GIF export). All asynchronous."""

import shutil

from gi.repository import Gio, GLib

from .imaging import png_to_pixbuf


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def _run(argv, callback):
    """Run argv; callback(stdout_bytes, None) or callback(None, error_message)."""
    try:
        proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
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
            message = err.get_data().decode(errors="replace").strip().splitlines()
            callback(None, message[-1] if message else "ffmpeg failed")
            return
        callback(out.get_data() if out else b"", None)
    proc.communicate_async(None, None, done)


def video_thumbnail(path, callback):
    """callback(pixbuf or None) with a frame from near the start of the video."""
    if not have_ffmpeg():
        callback(None)
        return

    def done(data, error):
        try:
            callback(png_to_pixbuf(data) if data else None)
        except GLib.Error:
            callback(None)
    _run(["ffmpeg", "-v", "error", "-ss", "0.3", "-i", str(path), "-frames:v", "1",
          "-f", "image2pipe", "-vcodec", "png", "-"], done)


def video_to_gif(src, dst, fps, max_width, callback):
    """callback(None) on success or callback(error_message)."""
    if not have_ffmpeg():
        callback("ffmpeg is not installed")
        return
    vf = (f"fps={fps},scale='min({max_width},iw)':-2:flags=lanczos,"
          "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-loop", "0", str(dst)],
         lambda _out, error: callback(error))
