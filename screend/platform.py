# SPDX-License-Identifier: GPL-3.0-or-later
"""Which desktop we're on, and compositor-specific helpers outside GNOME.

GNOME is served by the Shell extension (capture.ShellHelper). Everywhere else
we capture through the fastest available tool and ask the compositor for
window positions so the selector can snap to windows:

  KDE Plasma      KWin's ScreenShot2 D-Bus API (opt-in, see install.sh --kde-fast-capture)
  Hyprland        grim + hyprctl
  Sway / wlroots  grim + swaymsg
  anything else   xdg-desktop-portal
"""

import json
import os
import shutil
import subprocess


def desktop():
    current = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "gnome" in current:
        return "gnome"
    if "kde" in current:
        return "kde"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    return "other"


def have(tool):
    return shutil.which(tool) is not None


def _json(argv):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=2, check=True).stdout
        return json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


# -- window and monitor geometry (logical desktop coordinates) -----------------

def window_rects():
    """Visible windows, topmost first, as [(x, y, w, h), ...]; [] if unknown."""
    kind = desktop()
    if kind == "hyprland":
        return hyprland_windows(_json(["hyprctl", "-j", "clients"]), _json(["hyprctl", "-j", "monitors"]))
    if kind == "sway":
        return sway_windows(_json(["swaymsg", "-r", "-t", "get_tree"]))
    return []


def hyprland_windows(clients, monitors):
    if not clients:
        return []
    visible = {m.get("activeWorkspace", {}).get("id") for m in monitors or []}
    visible |= {m.get("specialWorkspace", {}).get("id") for m in monitors or []}
    wins = [c for c in clients if c.get("mapped", True) and not c.get("hidden")
            and c.get("workspace", {}).get("id") in visible]
    # focusHistoryID 0 is the most recently focused; floating windows sit above tiled ones
    wins.sort(key=lambda c: (not c.get("floating"), c.get("focusHistoryID", 0)))
    return [(c["at"][0], c["at"][1], c["size"][0], c["size"][1]) for c in wins]


def sway_windows(tree):
    if not tree:
        return []
    out = []

    def walk(node, floating=False):
        children = node.get("nodes", []) + node.get("floating_nodes", [])
        if not children and node.get("type") in ("con", "floating_con") and node.get("visible"):
            r = node["rect"]
            out.append((floating, r["x"], r["y"], r["width"], r["height"]))
        for child in node.get("nodes", []):
            walk(child, floating)
        for child in node.get("floating_nodes", []):
            walk(child, True)
    walk(tree)
    # floating windows are drawn above tiled ones
    out.sort(key=lambda t: not t[0])
    return [t[1:] for t in out]


def focused_window_rect():
    kind = desktop()
    if kind == "hyprland":
        c = _json(["hyprctl", "-j", "activewindow"])
        if c and "at" in c:
            return (c["at"][0], c["at"][1], c["size"][0], c["size"][1])
    elif kind == "sway":
        node = _find_focused(_json(["swaymsg", "-r", "-t", "get_tree"]))
        if node:
            r = node["rect"]
            return (r["x"], r["y"], r["width"], r["height"])
    return None


def _find_focused(node):
    if not node:
        return None
    if node.get("focused") and node.get("type") in ("con", "floating_con"):
        return node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        hit = _find_focused(child)
        if hit:
            return hit
    return None


def pointer_position():
    """Global pointer position if the compositor will tell us, else None."""
    if desktop() == "hyprland":
        pos = _json(["hyprctl", "-j", "cursorpos"])
        if pos:
            return pos["x"], pos["y"]
    return None


def focused_output_rect():
    if desktop() == "sway":
        for o in _json(["swaymsg", "-r", "-t", "get_outputs"]) or []:
            if o.get("focused"):
                r = o["rect"]
                return (r["x"], r["y"], r["width"], r["height"])
    return None


# -- layer shell (KDE, wlroots): place overlays without stealing focus ---------

_layer_shell = None


def layer_shell():
    """The Gtk4LayerShell module when usable on this desktop, else None."""
    global _layer_shell
    if _layer_shell is None:
        _layer_shell = False
        if desktop() not in ("gnome",):  # mutter does not implement wlr-layer-shell
            try:
                import gi
                gi.require_version("Gtk4LayerShell", "1.0")
                from gi.repository import Gtk4LayerShell
                if Gtk4LayerShell.is_supported():
                    _layer_shell = Gtk4LayerShell
            except (ImportError, ValueError):
                pass
    return _layer_shell or None
