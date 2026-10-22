# SPDX-License-Identifier: GPL-3.0-or-later
"""Editable list of Screend's global shortcuts (used by Welcome and Settings)."""

from gi.repository import Adw, Gdk, GLib, Gtk

from . import shortcuts

MODIFIERS = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
             | Gdk.ModifierType.SUPER_MASK)
# keys that make sense on their own, without a modifier
BARE_OK = {Gdk.KEY_Print} | {getattr(Gdk, f"KEY_F{i}") for i in range(1, 13)}


class ShortcutsGroup(Adw.PreferencesGroup):
    def __init__(self, title="Keyboard shortcuts", description=None):
        super().__init__(title=title)
        self.rows = {}
        if not shortcuts.available():
            self.set_description(description or "Screend can't set shortcuts on this desktop by itself.")
            label = Gtk.Label(label=shortcuts.instructions(), selectable=True, wrap=True, xalign=0,
                              css_classes=["monospace", "caption"], margin_top=6)
            self.add(label)
            return
        self.set_description(description or "Click a shortcut to change it.")
        defaults = Gtk.Button(label="Use defaults", valign=Gtk.Align.CENTER, css_classes=["flat"])
        defaults.connect("clicked", lambda *_: self.use_defaults())
        self.set_header_suffix(defaults)
        for action, desc, _default in shortcuts.DEFAULTS:
            row = Adw.ActionRow(title=desc, activatable=True)
            row.accel_label = Gtk.ShortcutLabel(disabled_text="Disabled", valign=Gtk.Align.CENTER)
            row.warning = Gtk.Image(icon_name="dialog-warning-symbolic", visible=False,
                                    css_classes=["warning"], valign=Gtk.Align.CENTER)
            row.add_suffix(row.warning)
            row.add_suffix(row.accel_label)
            row.connect("activated", lambda _r, a=action: self.edit(a))
            self.add(row)
            self.rows[action] = row
        self.refresh()

    @property
    def any_set(self):
        return any(shortcuts.current().values()) if shortcuts.available() else True

    def refresh(self):
        if not self.rows:
            return
        for action, accel in shortcuts.current().items():
            row = self.rows[action]
            row.accel_label.set_accelerator(accel or "")
            clash = shortcuts.conflicts(accel, action) if accel else None
            row.warning.set_visible(bool(clash))
            row.warning.set_tooltip_text(f"Also used by {clash}" if clash else None)

    def use_defaults(self):
        skipped = shortcuts.install_defaults()
        self.refresh()
        if skipped:
            names = ", ".join(shortcuts.DESCRIPTIONS[a].lower() for a, _k, _c in skipped)
            self.set_description(f"Skipped because the keys are taken: {names}. Click a row to pick other keys.")

    def edit(self, action):
        dialog = _CaptureDialog(action, self)
        dialog.present(self.get_root())


class _CaptureDialog(Adw.Dialog):
    """Asks for a new key combination; Backspace disables, Esc cancels."""

    def __init__(self, action, group):
        super().__init__(title="Set Shortcut", content_width=420)
        self.action, self.group = action, group
        self.pending = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24)
        box.append(Gtk.Label(label=shortcuts.DESCRIPTIONS[action], css_classes=["title-4"], wrap=True))
        self.preview = Gtk.ShortcutLabel(accelerator=shortcuts.current()[action] or "", disabled_text="—",
                                         halign=Gtk.Align.CENTER)
        box.append(self.preview)
        self.hint = Gtk.Label(label="Press the new shortcut.\nBackspace disables it, Esc cancels.",
                              justify=Gtk.Justification.CENTER, wrap=True, css_classes=["dim-label"])
        box.append(self.hint)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        self.set_child(view)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("map", self.on_map)
        self.connect("closed", self.on_closed)

    def on_map(self, *_):
        # let combinations like Shift+Super+S reach us instead of GNOME (it may ask once)
        surface = self.get_root().get_surface() if self.get_root() else None
        if surface and hasattr(surface, "inhibit_system_shortcuts"):
            surface.inhibit_system_shortcuts(None)

    def on_closed(self, *_):
        surface = self.group.get_root().get_surface() if self.group.get_root() else None
        if surface and hasattr(surface, "restore_system_shortcuts"):
            surface.restore_system_shortcuts()

    def on_key(self, _ctl, keyval, _code, state):
        mods = state & MODIFIERS
        if keyval == Gdk.KEY_Escape and not mods:
            self.close()
            return True
        if keyval == Gdk.KEY_BackSpace and not mods:
            self.apply(None)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not mods and self.pending:
            self.apply(self.pending)  # confirmed despite the clash
            return True
        if _is_modifier(keyval):
            return True
        if not mods and keyval not in BARE_OK:
            self.hint.set_label("Use at least one of Super, Ctrl, Alt or Shift.\nEsc cancels.")
            return True
        accel = Gtk.accelerator_name(Gdk.keyval_to_lower(keyval), mods)
        self.preview.set_accelerator(accel)
        clash = shortcuts.conflicts(accel, self.action)
        if clash:
            self.pending = accel
            self.hint.set_label(f"Already used by {clash}.\nPress another, or Enter to use it anyway.")
            return True
        self.apply(accel)
        return True

    def apply(self, accel):
        shortcuts.set_binding(self.action, accel)
        self.group.refresh()
        GLib.idle_add(lambda: self.close() and False)


def _is_modifier(keyval):
    name = Gdk.keyval_name(keyval) or ""
    return name.split("_")[0] in ("Shift", "Control", "Alt", "Super", "Meta", "Hyper", "ISO") or name in (
        "Caps_Lock", "Num_Lock")
