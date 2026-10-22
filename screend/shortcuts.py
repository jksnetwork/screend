# SPDX-License-Identifier: GPL-3.0-or-later
"""Global shortcuts, as GNOME custom keybindings.

GNOME runs the command for a custom keybinding itself, so Screend only has to
register `screend <action>` under the right accelerator. Other desktops bind
keys in their own configuration (see instructions()).

Also usable from the installer:  python3 -m screend.shortcuts install|remove|list
"""

import os
import shutil
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from . import platform  # noqa: E402

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM = MEDIA_KEYS + ".custom-keybinding"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

# (id, description, default accelerator); id is also the `screend` action
DEFAULTS = [
    ("region", "Capture an area, window or screen", "<Shift><Super>s"),
    ("window", "Capture the focused window", "<Shift><Super>w"),
    ("screen", "Capture the current screen", "<Shift><Super>d"),
    ("fullscreen", "Capture all screens", "<Shift><Super>a"),
    ("last", "Capture the previous area again", "<Shift><Super>r"),
    ("text", "Copy the text in an area", "<Shift><Super>t"),
    ("scroll", "Scrolling capture", "<Shift><Super>c"),
    ("record", "Start or stop recording", "<Shift><Super>v"),
]
DESCRIPTIONS = {d[0]: d[1] for d in DEFAULTS}

# GNOME's own shortcut lists, checked for clashes
SYSTEM_SCHEMAS = [
    ("org.gnome.desktop.wm.keybindings", "Windows"),
    ("org.gnome.shell.keybindings", "GNOME Shell"),
    ("org.gnome.mutter.keybindings", "Windows"),
    ("org.gnome.mutter.wayland.keybindings", "Windows"),
    (MEDIA_KEYS, "System"),
]


def available():
    """True when we can manage shortcuts here (GNOME with its media-keys schema)."""
    source = Gio.SettingsSchemaSource.get_default()
    return platform.desktop() == "gnome" and source is not None and source.lookup(MEDIA_KEYS, True) is not None


def _launcher():
    # set by bin/screend; GNOME runs keybinding commands without ~/.local/bin on PATH
    return os.environ.get("SCREEND_LAUNCHER") or shutil.which("screend") or "screend"


def _path(action):
    return f"{BASE}screend-{action}/"


def _entry(path):
    return Gio.Settings.new_with_path(CUSTOM, path)


def _paths():
    return list(Gio.Settings.new(MEDIA_KEYS).get_strv("custom-keybindings"))


def _set_paths(paths):
    Gio.Settings.new(MEDIA_KEYS).set_strv("custom-keybindings", paths)


def current():
    """{action: accelerator or None} for Screend's shortcuts."""
    paths = set(_paths())
    out = {}
    for action, _desc, _default in DEFAULTS:
        path = _path(action)
        binding = _entry(path).get_string("binding") if path in paths else ""
        out[action] = binding or None
    return out


def set_binding(action, accel):
    """Bind `action` to `accel`; None removes the shortcut."""
    path = _path(action)
    paths = _paths()
    if accel:
        entry = _entry(path)
        entry.set_string("name", f"Screend: {DESCRIPTIONS[action].lower()}")
        entry.set_string("command", f"{_launcher()} {action}")
        entry.set_string("binding", accel)
        if path not in paths:
            _set_paths(paths + [path])
    else:
        if path in paths:
            _set_paths([p for p in paths if p != path])
        _entry(path).reset("name")
        _entry(path).reset("command")
        _entry(path).reset("binding")
    Gio.Settings.sync()


def install_defaults():
    """Add every default shortcut that doesn't clash with something else. Returns the skipped ones."""
    _remove_legacy()
    skipped = []
    have = current()
    for action, _desc, accel in DEFAULTS:
        if have[action]:
            continue
        clash = conflicts(accel, action)
        if clash:
            skipped.append((action, accel, clash))
        else:
            set_binding(action, accel)
    return skipped


def remove_all():
    _remove_legacy()
    for action, _desc, _default in DEFAULTS:
        set_binding(action, None)


def _remove_legacy():
    # early installers used numbered paths: .../custom-keybindings/screend0/ etc.
    legacy = [p for p in _paths() if p.startswith(BASE + "screend") and p.rstrip("/")[len(BASE):].removeprefix(
        "screend").isdigit()]
    if legacy:
        _set_paths([p for p in _paths() if p not in legacy])
        for path in legacy:
            for key in ("name", "command", "binding"):
                _entry(path).reset(key)


# -- clashes --------------------------------------------------------------------

def _normalize(accel):
    """Comparable form of an accelerator, or None if it doesn't parse."""
    from gi.repository import Gdk, Gtk
    ok, key, mods = Gtk.accelerator_parse(accel)
    if not ok or not key:
        return None
    return Gdk.keyval_to_lower(key), int(mods)


def conflicts(accel, ignore_action=None):
    """Who else uses `accel`: a short description, or None."""
    target = _normalize(accel)
    if target is None:
        return None
    source = Gio.SettingsSchemaSource.get_default()
    for schema_id, area in SYSTEM_SCHEMAS:
        schema = source.lookup(schema_id, True)
        if schema is None:
            continue
        settings = Gio.Settings.new(schema_id)
        for key in schema.list_keys():
            value = settings.get_value(key)
            if value.get_type_string() == "as":
                accels = value.unpack()
            elif value.get_type_string() == "s" and key not in ("custom-keybindings",):
                accels = [value.unpack()]
            else:
                continue
            if any(_normalize(a) == target for a in accels if a):
                return f"{area}: {schema.get_key(key).get_summary() or key}"
    ours = {_path(a) for a, _d, _k in DEFAULTS}
    for path in _paths():
        entry = _entry(path)
        if path == (_path(ignore_action) if ignore_action else None):
            continue
        if entry.get_string("binding") and _normalize(entry.get_string("binding")) == target:
            name = entry.get_string("name") or "a custom shortcut"
            return name if path in ours else f"Custom shortcut: {name}"
    return None


# -- other desktops -------------------------------------------------------------

def instructions():
    """How to bind keys where Screend can't do it itself."""
    launcher = _launcher()
    kind = platform.desktop()
    if kind == "kde":
        return ("KDE Plasma picks up Screend's default shortcuts (Meta+Shift+S and friends) from its "
                "menu entry. Change them in System Settings → Keyboard → Shortcuts → Screend.")
    if kind == "hyprland":
        lines = [f"bind = SUPER SHIFT, {accel[-1].upper()}, exec, {launcher} {action}"
                 for action, _d, accel in DEFAULTS]
        return "Add these to ~/.config/hypr/hyprland.conf:\n\n" + "\n".join(lines)
    if kind == "sway":
        lines = [f"bindsym $mod+Shift+{accel[-1]} exec {launcher} {action}" for action, _d, accel in DEFAULTS]
        return "Add these to your Sway config:\n\n" + "\n".join(lines)
    return f"Bind keys to commands like `{launcher} region` in your desktop's keyboard settings."


def main(argv):
    command = argv[1] if len(argv) > 1 else "list"
    if not available():
        print(instructions())
        return 0
    if command == "install":
        for action, accel, clash in install_defaults():
            print(f"  skipped {accel} for '{action}': already used by {clash}")
        for action, accel in current().items():
            if accel:
                print(f"  {accel:<20} screend {action}")
    elif command == "remove":
        remove_all()
        print("Screend shortcuts removed.")
    else:
        for action, accel in current().items():
            print(f"  {accel or '(none)':<20} {DESCRIPTIONS[action]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
