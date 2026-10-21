# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen recording through the ScreenCast portal + GStreamer (any Wayland desktop).

The portal asks the user which screen or window to share; with persist_mode it
hands back a restore token so later recordings start without asking again.
GNOME with the Screend extension uses GNOME's own recorder instead (see app.py).
"""

import itertools
import os
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
SOURCE_MONITOR, SOURCE_WINDOW = 1, 2
CURSOR_HIDDEN, CURSOR_EMBEDDED = 1, 2
_tokens = itertools.count(1)


class PortalRecorder:
    def __init__(self, config):
        self.config = config
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.session = None
        self.pipeline = None
        self.path = None
        self.on_finished = None
        Gst.init(None)

    @property
    def active(self):
        return self.pipeline is not None

    # -- portal request plumbing ----------------------------------------------

    def _request(self, method, args, options, callback):
        """Call a portal method that answers through a Request object's Response signal."""
        token = f"screend_rec{os.getpid()}_{next(_tokens)}"
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{token}"
        options = {**options, "handle_token": GLib.Variant("s", token)}
        sub = None

        def on_response(_c, _s, _p, _i, _sig, params):
            self.bus.signal_unsubscribe(sub)
            code, results = params.unpack()
            callback(results if code == 0 else None, "cancelled" if code == 1 else f"portal error {code}")

        sub = self.bus.signal_subscribe(PORTAL_BUS, "org.freedesktop.portal.Request", "Response",
                                        path, None, Gio.DBusSignalFlags.NONE, on_response)
        signature = "(" + "".join(sig for sig, _ in args) + "a{sv})"
        values = tuple(v for _, v in args) + (options,)

        def sent(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error as e:
                self.bus.signal_unsubscribe(sub)
                callback(None, e.message)
        self.bus.call(PORTAL_BUS, PORTAL_PATH, SCREENCAST, method, GLib.Variant(signature, values),
                      None, Gio.DBusCallFlags.NONE, -1, None, sent)

    # -- start ----------------------------------------------------------------

    def start(self, callback, on_finished):
        """callback(True, None) once recording, or callback(False, error).

        on_finished(path or None) runs when the recording ends for any reason:
        stop(), the user ending the share from the desktop, or an error.
        """
        self.on_finished = on_finished
        def fail(error):
            self._close_session()
            callback(False, error)

        def created(results, error):
            if results is None:
                return fail(error)
            self.session = results["session_handle"]
            options = {
                "types": GLib.Variant("u", SOURCE_MONITOR | SOURCE_WINDOW),
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED if self.config.record_cursor else CURSOR_HIDDEN),
                "persist_mode": GLib.Variant("u", 2),
            }
            if self.config.portal_restore_token:
                options["restore_token"] = GLib.Variant("s", self.config.portal_restore_token)
            self._request("SelectSources", [("o", self.session)], options, selected)

        def selected(results, error):
            if results is None:
                return fail(error)
            self._request("Start", [("o", self.session), ("s", "")], {}, started)

        def started(results, error):
            if results is None:
                return fail(error)
            if "restore_token" in results:
                self.config.portal_restore_token = results["restore_token"]
                self.config.save()
            streams = results.get("streams") or []
            if not streams:
                return fail("nothing was shared")
            node_id = streams[0][0]
            self._open_remote(node_id, callback, fail)

        session_token = f"screend_session{next(_tokens)}"
        self._request("CreateSession", [], {"session_handle_token": GLib.Variant("s", session_token)}, created)

    def _open_remote(self, node_id, callback, fail):
        def done(conn, res):
            try:
                result, fds = conn.call_with_unix_fd_list_finish(res)
            except GLib.Error as e:
                return fail(e.message)
            fd = fds.get(result.unpack()[0])
            try:
                self._start_pipeline(fd, node_id)
            except GLib.Error as e:
                os.close(fd)
                return fail(e.message)
            callback(True, None)

        self.bus.call_with_unix_fd_list(PORTAL_BUS, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
                                        GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType("(h)"),
                                        Gio.DBusCallFlags.NONE, -1, None, None, done)

    def _start_pipeline(self, fd, node_id):
        out_dir = Path(GLib.get_user_runtime_dir()) / "screend"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / time.strftime("recording-%Y%m%d-%H%M%S.webm")
        # VP8/WebM copes with odd frame sizes (window shares often have them) and plays everywhere
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=1000 always-copy=true "
            "! queue ! videoconvert ! videorate ! video/x-raw,framerate=30/1 "
            "! vp8enc deadline=1 cpu-used=8 threads=4 end-usage=cbr target-bitrate=8000000 "
            f"! queue ! webmmux ! filesink location=\"{self.path}\"")
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", lambda *_: self._finish())
        bus.connect("message::error", self._on_error)
        self.pipeline.set_state(Gst.State.PLAYING)

    # -- stop -----------------------------------------------------------------

    def stop(self):
        """Finalise the file; on_finished fires once it is complete."""
        if self.pipeline is None:
            return
        self.pipeline.send_event(Gst.Event.new_eos())
        # never hang if the source stopped producing frames
        GLib.timeout_add_seconds(5, lambda: self.pipeline is not None and self._finish() and False)

    def _on_error(self, _bus, message):
        err, _debug = message.parse_error()
        print(f"screend: recording stopped: {err.message}")
        # usually the share was ended from the desktop: keep whatever was recorded
        self._finish()

    def _finish(self):
        if self.pipeline is None:
            return False
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self._close_session()
        ok = self.path and self.path.exists() and self.path.stat().st_size > 0
        if self.on_finished:
            self.on_finished(str(self.path) if ok else None)
        return False

    def _close_session(self):
        if self.session:
            self.bus.call(PORTAL_BUS, self.session, "org.freedesktop.portal.Session", "Close",
                          None, None, Gio.DBusCallFlags.NONE, -1, None, None)
            self.session = None
