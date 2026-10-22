# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional helpers Screend can use, and how to install them on common distros."""

import shutil
from pathlib import Path

# id -> (what it enables, executable/typelib to look for, {package manager: packages})
EXTRAS = {
    "tesseract": ("Copy text from screenshots (OCR)", "tesseract",
                  {"apt": "tesseract-ocr", "dnf": "tesseract", "pacman": "tesseract tesseract-data-eng",
                   "zypper": "tesseract-ocr"}),
    "ffmpeg": ("Export recordings as GIFs", "ffmpeg",
               {"apt": "ffmpeg", "dnf": "ffmpeg-free", "pacman": "ffmpeg", "zypper": "ffmpeg"}),
    "grim": ("Fast capture on Sway and Hyprland", "grim",
             {"apt": "grim", "dnf": "grim", "pacman": "grim", "zypper": "grim"}),
    "layer-shell": ("Corner card and pins on KDE, Sway and Hyprland", "typelib:Gtk4LayerShell",
                    {"dnf": "gtk4-layer-shell", "pacman": "gtk4-layer-shell", "zypper": "typelib-1_0-Gtk4LayerShell-1_0"}),
}

INSTALL = {"apt": "sudo apt install", "dnf": "sudo dnf install", "pacman": "sudo pacman -S",
           "zypper": "sudo zypper install"}


def os_release():
    try:
        lines = Path("/etc/os-release").read_text().splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        key, _, value = line.partition("=")
        out[key] = value.strip().strip('"')
    return out


def package_manager():
    info = os_release()
    family = " ".join([info.get("ID", ""), info.get("ID_LIKE", "")]).lower()
    for words, manager in [(("debian", "ubuntu"), "apt"), (("fedora", "rhel"), "dnf"),
                           (("arch",), "pacman"), (("suse",), "zypper")]:
        if any(w in family for w in words):
            return manager
    return None


def installed(extra):
    probe = EXTRAS[extra][1]
    if probe.startswith("typelib:"):
        import gi
        try:
            gi.require_version(probe[8:], "1.0")
            return True
        except ValueError:
            return False
    return shutil.which(probe) is not None


def install_command(extra):
    """The command to install `extra` here, or None if we don't know it."""
    manager = package_manager()
    packages = EXTRAS[extra][2].get(manager) if manager else None
    return f"{INSTALL[manager]} {packages}" if packages else None


def relevant(desktop):
    """Extras worth mentioning on this desktop."""
    extras = ["tesseract", "ffmpeg"]
    if desktop in ("sway", "hyprland"):
        extras.append("grim")
    if desktop in ("kde", "sway", "hyprland"):
        extras.append("layer-shell")
    return extras
