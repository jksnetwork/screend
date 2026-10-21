# SPDX-License-Identifier: GPL-3.0-or-later
"""User settings, stored as JSON in ~/.config/screend/config.json."""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from gi.repository import GLib

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "screend"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _default_save_dir():
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return str(Path(pictures or Path.home() / "Pictures") / "Screend")


@dataclass
class Config:
    save_dir: str = ""
    # strftime pattern for new captures
    filename_format: str = "Screend %Y-%m-%d at %H.%M.%S.png"
    # every capture also goes straight into save_dir (otherwise only when you click Save)
    auto_save: bool = False
    # recent captures kept in ~/.cache/screend/history for the History window; 0 = none
    history_limit: int = 50
    # off: GNOME ignores clipboard requests from unfocused apps, and copying
    # belongs after annotating anyway (card Copy button or Ctrl+C in the editor)
    auto_copy: bool = False
    show_quick_access: bool = True
    show_tray: bool = True
    # keep the area selection editable after the drag; Enter captures (GNOME extension only)
    adjust_selection: bool = False
    # seconds before the quick access card hides itself; 0 keeps it until dismissed
    quick_access_timeout: int = 8
    # screen recording
    record_cursor: bool = True
    gif_fps: int = 15
    gif_max_width: int = 960
    # lets the ScreenCast portal reuse the last screen/window choice without asking
    portal_restore_token: str = ""
    # text recognition: Tesseract language codes, e.g. "eng" or "eng+deu"
    ocr_language: str = "eng"
    # annotation defaults
    color: str = "#ff3b30"
    stroke: int = 1  # 0 small, 1 medium, 2 large
    filled: bool = False
    # editor "background" (beautify) settings, remembered between captures
    bg_style: str = "none"
    bg_padding: int = 64
    bg_radius: int = 12
    bg_shadow: bool = True

    def __post_init__(self):
        if not self.save_dir:
            self.save_dir = _default_save_dir()

    @property
    def save_path(self) -> Path:
        return Path(os.path.expanduser(self.save_dir))

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            data = {}
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        if not CONFIG_FILE.exists():
            cfg.save()
        return cfg

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2) + "\n")
