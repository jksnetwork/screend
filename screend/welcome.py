# SPDX-License-Identifier: GPL-3.0-or-later
"""First-run setup: shortcuts, start at login, GNOME extension, optional extras."""

from gi.repository import Adw, Gio, GLib, Gtk

from . import deps, platform, shortcuts
from .settings import autostart_state, set_autostart
from .shortcuts_ui import ShortcutsGroup

EXTENSION_UUID = "screend@jksnetwork.github.io"


def extension_state(app):
    """'active', 'pending' (enabled, waiting for a login), 'disabled', 'missing' or 'n/a'."""
    if platform.desktop() != "gnome":
        return "n/a"
    if app.shell.available():
        return "active"
    dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
    installed = any(GLib.file_test(f"{d}/gnome-shell/extensions/{EXTENSION_UUID}", GLib.FileTest.IS_DIR)
                    for d in dirs)
    if not installed:
        return "missing"
    enabled = EXTENSION_UUID in Gio.Settings.new("org.gnome.shell").get_strv("enabled-extensions")
    return "pending" if enabled else "disabled"


def enable_extension():
    settings = Gio.Settings.new("org.gnome.shell")
    current = list(settings.get_strv("enabled-extensions"))
    if EXTENSION_UUID not in current:
        settings.set_strv("enabled-extensions", current + [EXTENSION_UUID])


class WelcomeWindow(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app, title="Welcome to Screend", default_width=620, default_height=760)
        self.app = app
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_title=False))

        page = Adw.PreferencesPage()
        hero = Adw.StatusPage(icon_name="applets-screenshooter", title="Welcome to Screend",
                              description="Screenshots, annotation, scrolling capture, text recognition "
                                          "and screen recording. A few things to set up:")
        hero.add_css_class("compact")
        hero_group = Adw.PreferencesGroup()
        hero_group.add(hero)
        page.add(hero_group)

        self.keys = ShortcutsGroup(description=None)
        if shortcuts.available() and not self.keys.any_set:
            # offer the defaults up front on first run
            self.keys.use_defaults()
        page.add(self.keys)

        startup = Adw.PreferencesGroup(title="Start automatically")
        state = autostart_state()
        row = Adw.SwitchRow(title="Start Screend when you log in",
                            subtitle="Keeps the tray icon and instant shortcuts ready.",
                            active=state != "disabled", sensitive=state is not None)
        if state is None:
            row.set_subtitle("Unavailable: Screend's login service isn't installed.")
        else:
            set_autostart(row.get_active())
            row.connect("notify::active", lambda r, _p: set_autostart(r.get_active()))
        startup.add(row)
        page.add(startup)

        ext = self.extension_group()
        if ext:
            page.add(ext)

        extras = Adw.PreferencesGroup(title="Optional extras",
                                      description="Screend works without these; each one unlocks a feature.")
        for extra in deps.relevant(platform.desktop()):
            extras.add(self.extra_row(extra))
        page.add(extras)

        done = Gtk.Button(label="Get Started", halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=24,
                          css_classes=["suggested-action", "pill"])
        done.connect("clicked", lambda *_: self.finish())
        done_group = Adw.PreferencesGroup()
        done_group.add(done)
        page.add(done_group)

        view.set_content(page)
        self.set_content(view)
        # GTK scrolls to the first focusable row on map; start at the top instead
        self.connect("map", lambda *_: GLib.idle_add(lambda: page.scroll_to_top() and False))
        self.connect("close-request", lambda *_: self.finish(close=False) or False)

    def extension_group(self):
        state = extension_state(self.app)
        if state == "disabled" and not self.app.config.welcomed:
            # installed by the package but not yet on for this user: that's what they want
            enable_extension()
            state = "pending"
        if state == "n/a":
            return None
        group = Adw.PreferencesGroup(title="GNOME integration",
                                     description="The Screend extension makes capture instant, snaps to "
                                                 "windows, and keeps the card and pins on top.")
        texts = {
            "active": ("Extension active", "Everything is ready."),
            "pending": ("Almost there", "Log out and back in once to load the extension."),
            "disabled": ("Extension installed but turned off", "Turn it on, then log out and back in."),
            "missing": ("Extension not installed", "Reinstall Screend to get it. Screend still works without it, "
                                                   "just more slowly."),
        }
        title, subtitle = texts[state]
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        icon = {"active": "emblem-ok-symbolic", "pending": "system-log-out-symbolic"}.get(
            state, "dialog-warning-symbolic")
        row.add_prefix(Gtk.Image(icon_name=icon))
        if state == "disabled":
            button = Gtk.Button(label="Turn On", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])

            def turn_on(*_):
                enable_extension()
                row.set_title(texts["pending"][0])
                row.set_subtitle(texts["pending"][1])
                button.set_visible(False)
            button.connect("clicked", turn_on)
            row.add_suffix(button)
        group.add(row)
        return group

    def extra_row(self, extra):
        what = deps.EXTRAS[extra][0]
        row = Adw.ActionRow(title=what)
        if deps.installed(extra):
            row.set_subtitle("Installed")
            row.add_suffix(Gtk.Image(icon_name="emblem-ok-symbolic", css_classes=["success"]))
            return row
        command = deps.install_command(extra)
        row.set_subtitle(command or "Install it with your package manager")
        row.set_subtitle_selectable(True)
        if command:
            copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy command",
                              valign=Gtk.Align.CENTER, css_classes=["flat"])
            copy.connect("clicked", lambda *_: (self.get_clipboard().set(command),
                                                copy.set_icon_name("emblem-ok-symbolic")))
            row.add_suffix(copy)
        return row

    def finish(self, close=True):
        self.app.config.welcomed = True
        self.app.config.save()
        if close:
            self.close()
