# SPDX-License-Identifier: GPL-3.0-or-later
"""Preferences window, editing the same Config the rest of the app uses."""

import subprocess

from gi.repository import Adw, Gio, GLib, Gtk

from . import deps, ocr
from .shortcuts_ui import ShortcutsGroup

UNIT = "screend.service"


def _pretty_path(path):
    """Show paths under the home folder as ~/…"""
    path, home = str(path), GLib.get_home_dir()
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _systemctl(*args):
    """Output of `systemctl --user ARGS` (stripped), or None if it failed to run or the unit is unknown."""
    try:
        result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    if args[0] == "is-enabled":
        return out if out in ("enabled", "disabled") else None
    return out if result.returncode == 0 else None


# Start at login is the systemd user unit set up by install.sh or the package.
# Toggling it never stops the running instance.

def autostart_state():
    """"enabled", "disabled", or None when the login service isn't installed."""
    return _systemctl("is-enabled", UNIT)


def set_autostart(on):
    return _systemctl("enable" if on else "disable", UNIT) is not None


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Screend Settings", default_width=640, default_height=720,
                         search_enabled=False)
        self.app = app
        self.config = app.config
        self.connect("close-request", lambda *_: self.config.save() or False)

        page = Adw.PreferencesPage(title="General", icon_name="emblem-system-symbolic")
        self.add(page)

        saving = Adw.PreferencesGroup(title="Saving")
        page.add(saving)
        self.folder_row = Adw.ActionRow(title="Save folder", subtitle=_pretty_path(self.config.save_path))
        choose = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Choose folder", css_classes=["flat"])
        choose.connect("clicked", self.choose_folder)
        self.folder_row.add_suffix(choose)
        saving.add(self.folder_row)
        saving.add(self.entry("File name pattern", "filename_format",
                              "strftime codes, e.g. %Y-%m-%d"))
        saving.add(self.switch("Save every capture automatically", "auto_save",
                               "Otherwise only when you click Save"))
        saving.add(self.spin("Recent captures to keep", "history_limit", 0, 500,
                             "Shown in History; older ones are deleted. 0 keeps none"))

        general = Adw.PreferencesGroup(title="General")
        page.add(general)
        general.add(self.autostart_row())
        general.add(self.switch("Show tray icon", "show_tray", "Takes effect after restarting Screend"))

        after = Adw.PreferencesGroup(title="After capturing")
        page.add(after)
        after.add(self.switch("Show quick access card", "show_quick_access"))
        after.add(self.spin("Hide card after (seconds)", "quick_access_timeout", 0, 120,
                            "0 keeps it until dismissed"))
        after.add(self.switch("Copy to clipboard immediately", "auto_copy",
                              "Reliable only with the GNOME extension"))
        after.add(self.switch("Adjust selection before capturing", "adjust_selection",
                              "Press Enter to capture; GNOME extension only"))

        text = Adw.PreferencesGroup(title="Text recognition")
        page.add(text)
        lang = self.entry("Languages", "ocr_language")
        text.add(lang)
        if not ocr.available():
            command = deps.install_command("tesseract")
            text.set_description(f"Install Tesseract to enable: {command}" if command
                                 else "Install Tesseract with your package manager to enable it")
        else:
            text.set_description("Tesseract language codes joined with +, e.g. eng+deu")

        recording = Adw.PreferencesGroup(title="Recording")
        page.add(recording)
        recording.add(self.switch("Show mouse pointer", "record_cursor"))
        recording.add(self.spin("GIF frame rate", "gif_fps", 5, 30))
        recording.add(self.spin("GIF maximum width", "gif_max_width", 240, 1920))

        keys_page = Adw.PreferencesPage(title="Shortcuts", icon_name="preferences-desktop-keyboard-shortcuts-symbolic")
        self.add(keys_page)
        self.keys_page = keys_page
        keys_page.add(ShortcutsGroup(title="Global shortcuts"))
        status = Adw.PreferencesGroup(title="GNOME extension")
        keys_page.add(status)
        active = app.shell.available()
        status.add(Adw.ActionRow(
            title="Active" if active else "Not running",
            subtitle="Fast capture, window snapping, recording and floating windows are available"
            if active else "Log out and back in after installing to enable fast capture and recording"))
        welcome = Adw.ButtonRow(title="Show Welcome Screen") if hasattr(Adw, "ButtonRow") else None
        if welcome is None:
            welcome = Adw.ActionRow(title="Show welcome screen", activatable=True)
            welcome.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        welcome.connect("activated", lambda *_: app.open_welcome())
        status.add(welcome)

    # -- start at login -------------------------------------------------------
    #
    # install.sh sets up a systemd user unit; this switch enables/disables it.
    # It never stops the running instance.

    def autostart_row(self):
        row = Adw.SwitchRow(title="Start at login",
                            subtitle="Keeps the tray icon and instant hotkeys ready. "
                                     "When off, the first hotkey starts Screend.")
        state = autostart_state()
        if state is None:
            row.set_sensitive(False)
            row.set_subtitle("Unavailable: Screend's login service isn't installed")
            return row
        row.set_active(state == "enabled")
        row.connect("notify::active", self.on_autostart_toggled)
        return row

    def on_autostart_toggled(self, row, _pspec):
        if not set_autostart(row.get_active()):
            self.app.notify_error("Could not change start at login")

    # -- row builders -------------------------------------------------------

    def switch(self, title, key, subtitle=None):
        row = Adw.SwitchRow(title=title, active=getattr(self.config, key))
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect("notify::active", lambda r, _p: self.set(key, r.get_active()))
        return row

    def spin(self, title, key, lo, hi, subtitle=None):
        row = Adw.SpinRow.new_with_range(lo, hi, 1)
        row.set_title(title)
        row.set_value(getattr(self.config, key))
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect("notify::value", lambda r, _p: self.set(key, int(r.get_value())))
        return row

    def entry(self, title, key, subtitle=None):
        row = Adw.EntryRow(title=title, text=getattr(self.config, key), show_apply_button=True)
        row.connect("apply", lambda r: self.set(key, r.get_text() or getattr(self.config, key)))
        return row

    def set(self, key, value):
        setattr(self.config, key, value)
        self.config.save()

    def choose_folder(self, _btn):
        dialog = Gtk.FileDialog(title="Choose save folder")
        if self.config.save_path.exists():
            dialog.set_initial_folder(Gio.File.new_for_path(str(self.config.save_path)))

        def done(dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            self.set("save_dir", folder.get_path())
            self.folder_row.set_subtitle(_pretty_path(folder.get_path()))
        dialog.select_folder(self, None, done)
