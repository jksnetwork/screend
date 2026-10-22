# Contributing to Screend

Thanks for helping. Testing on desktops other than GNOME is especially valuable right now: KDE Plasma, Hyprland and Sway support is written against those desktops' documented APIs but has seen little real use.

## Reporting bugs

Please include:
- your distribution, desktop and version
- what you did, what you expected, and what happened
- the debug info from **About Screend → Troubleshooting → Save debug info**

If the GNOME extension misbehaves, the shell's log helps: `journalctl --user -b | grep -i screend`.

## Running from a checkout

```bash
bin/screend quit            # stop an installed instance, if one is running
bin/screend daemon          # run this checkout in the foreground
bin/screend region          # in another terminal: trigger actions
```

`bin/screend` uses the checkout when it sits next to the `screend/` package. `./install.sh` copies the app to `~/.local/share/screend` and the extension to `~/.local/share/gnome-shell/extensions/`.

Requirements: Python 3.10+, PyGObject, GTK 4 (4.12+), libadwaita (1.5+), pycairo and Pillow. Optional: tesseract, ffmpeg, grim, gtk4-layer-shell.

## How it fits together

```
 hotkey / tray / CLI ──► screend <action> ──D-Bus──► resident Screend app (GTK 4)
                                                     │
               ┌─────────────────────────────────────┼──────────────────────────────┐
               ▼                                     ▼                              ▼
   GNOME: Shell extension                 KDE: KWin ScreenShot2            others: xdg-desktop-portal
   (freeze, select, encode, record,       wlroots: grim + hyprctl/swaymsg  (Screenshot, ScreenCast)
    place windows, clipboard)
```

- **The app is a single resident instance** (`Gio.Application`), started at login by a systemd user unit. Later `screend …` invocations forward their arguments to it and exit. Staying resident keeps hotkeys instant, and keeps clipboard contents alive (on Wayland they vanish when the owning app exits).
- **Capture backends** are chosen at run time:
  - **GNOME Shell extension**: freezes the stage in-process, runs the selector inside the shell, and encodes only the selected area. This is why it's instant.
  - **KWin's `org.kde.KWin.ScreenShot2`**: raw pixels over a pipe.
  - **`grim`** on wlroots compositors.
  - **xdg-desktop-portal** everywhere else.
- **Wayland window placement** is handled per desktop. Apps can't position their own windows, so the corner card and pins are placed by the extension on GNOME and by layer-shell on KDE and wlroots. Otherwise the card falls back to a transparent maximized window with a shrunken input region.

### Source layout

| Path | What's in it |
|---|---|
| `screend/app.py` | Application, command-line actions, capture flows, delivery to card/editor/OCR |
| `screend/capture.py` | Portal, grim and KWin capture; `ShellHelper` (the extension's D-Bus client) |
| `screend/platform.py` | Desktop detection, compositor window geometry, layer-shell |
| `screend/selector.py` | The portable GTK area selector (used when the extension isn't available) |
| `screend/quickaccess.py` | The card |
| `screend/editor.py`, `annotations.py` | Annotation editor and shapes |
| `screend/scrolling.py`, `stitch.py` | Scrolling capture loop and the stitching algorithm |
| `screend/ocr.py` | Tesseract wrapper |
| `screend/recorder.py`, `media.py` | Portal + GStreamer recording; ffmpeg thumbnails and GIFs |
| `screend/pin.py`, `history.py`, `settings.py`, `tray.py` | Pins, history window, preferences, StatusNotifierItem tray icon |
| `screend/shot.py`, `config.py`, `imaging.py` | Capture model and history storage, settings, image conversions |
| `gnome-extension/` | The GNOME Shell extension |
| `tools/` | Nested-shell test harness and the docs screenshot generator |
| `tests/` | Unit tests |

### The extension's D-Bus API

Bus name `io.github.jksnetwork.Screend.Shell`, object `/io/github/jksnetwork/Screend/Shell`, interface `io.github.jksnetwork.Screend.Shell`. The app checks `Version` (currently 4). Captured images are PNG files in `$XDG_RUNTIME_DIR/screend/`; the caller deletes them.

| Member | Signature | Purpose |
|---|---|---|
| `SelectArea(adjust)` | `b → s path, s kind, (iiii) rect` | Interactive selector; `kind` is `area`, `window`, `screen` or `cancelled` |
| `CaptureRect(rect)` | `(iiii) → s` | Capture a logical-coordinate rectangle |
| `CaptureMonitor()` | `→ s, (iiii)` | The monitor under the pointer |
| `CaptureAll()` | `→ s` | Every monitor |
| `CaptureFocusedWindow()` | `→ s` | The focused window's own pixels (unobscured) |
| `StartRecording(mode, adjust, cursor)` | `sbb → b` | `mode` is `select` or `screen`; uses GNOME's recorder |
| `StopRecording()` | | |
| `RecordingFinished` (signal) | `s path` | Emitted once the file is finalised |
| `SetClipboardImage(path)`, `SetClipboardText(text)` | `s` | Set the clipboard without having focus |
| `ShowFrame(rect)`, `HideFrame()` | `(iiii)` | Outline an area (scrolling capture) |
| `ManageWindow(title, mode, x, y)` | `ssii` | Rule for Screend's own windows: `card` (bottom-left, above, focus handed back), `pin` (above, all workspaces), `focus` (raise on open) |
| `Version`, `Recording` (properties) | `u`, `b` | |

The recording support drives private parts of GNOME Shell's screenshot UI (`Main.screenshotUI`). Expect to revisit it when GNOME changes.

## Tests

```bash
python3 -m unittest discover tests
```

The unit tests cover stitching (synthetic pages with sticky headers and footers), history pruning, OCR helpers, and window-geometry parsing for Hyprland and Sway. Please add tests next to anything you change that doesn't need a display.

### Working on the GNOME extension without logging out

GNOME on Wayland only loads extensions at login. Instead, run a nested GNOME Shell in a window:

```bash
cp gnome-extension/* ~/.local/share/gnome-shell/extensions/screend@jksnetwork.github.io/
tools/nested-shell.sh bash        # a shell whose apps run inside the nested GNOME
python3 -m screend daemon &       # (from the repo) talks to the nested extension
python3 -m screend region
```

The nested session has its own D-Bus bus, in-memory settings and an empty Desktop folder, so nothing touches your real session. GNOME's own log goes to `/tmp/screend-nested-*.log`.

## Regenerating the documentation images

The screenshots and the GIF in `docs/images/` come from a script, so they never contain anyone's personal files:

```bash
tools/nested-shell.sh python3 tools/docs_screenshots.py
```

It drives Screend through each feature in a nested session, with its own temporary config, history and demo content. It needs `ffmpeg` for the GIF. Update the script when the UI changes, rather than adding hand-made screenshots.

## Packaging and releases

- `packaging/build-deb.sh` builds `dist/screend_<version>_all.deb`. It installs to `/usr/lib/screend`, `/usr/bin/screend`, the system extension and systemd user unit directories, and AppStream metadata. Dependencies are declared in the script.
- `install.sh` is the per-user installer. Piped from curl it downloads the latest release tarball first, then checks dependencies and installs into `~/.local`.
- **Releasing:** bump `__version__` in `screend/__init__.py` and add a `<release>` to `data/io.github.jksnetwork.Screend.metainfo.xml`, then push a tag `v<version>`. The *Release* workflow checks the tag matches the version, builds the `.deb` and publishes a GitHub release with it attached.
- The *CI* workflow runs the tests, validates the desktop entry and AppStream data, and builds the package on every push and pull request.

## Code style

- Match the surrounding code: small modules, docstrings that explain *why*, comments only where something isn't obvious.
- Python is formatted for readability at about 110 columns. JavaScript follows GNOME Shell's style.
- Anything slow (encoding, OCR, subprocesses) runs asynchronously; the UI thread must never block.
