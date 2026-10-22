# Screend

**Fast screenshots, annotation and screen recording for Linux on Wayland.**

Press a key, drag over what you want, and a small card appears. Copy it, drag it into a chat, mark it up, pin it on top of your work, or pull the text out of it. Screend runs natively on GNOME through a small Shell extension, and also works on KDE Plasma, Hyprland and Sway.

![Selecting an area: the screen freezes, everything outside the selection dims, and a magnifier shows exact pixels](docs/images/selector.png)

## Highlights

### Capture exactly what you mean
The screen freezes the instant you press the shortcut. Drag out an area across any monitor, or click to grab the window or screen under the pointer. A magnifier and live size readout help with pixel-exact selections.

### A card instead of a file dialog
Every capture lands in a card in the corner of the screen. Hover it to **copy**, **save**, **annotate**, **pin**, or **copy the text**, or just drag the thumbnail into Slack, a browser or your file manager. The card stays out of the way and doesn't take keyboard focus.

![The quick access card in the bottom-left corner, with Copy, Save and tool buttons](docs/images/card.png)

### Mark it up
Arrows, boxes, ellipses, lines, pen, highlighter, text, numbered steps, blur, pixelate, spotlight and crop. Every shape can be moved, resized and recoloured, and there's undo for everything.

![The annotation editor with a highlight, a box, an arrow with a label, numbered steps and blurred email addresses](docs/images/editor.png)

### Make it presentable
One click puts the screenshot on a gradient or solid background, with padding, rounded corners and a soft shadow. Ready for docs, slides and social posts.

| Background options | The result |
|---|---|
| ![The background panel with gradient swatches, padding, corner and shadow controls](docs/images/background.png) | ![An annotated screenshot on a purple gradient with rounded corners and a shadow](docs/images/beautified.png) |

### Scrolling capture
Need the whole page, chat or document? Select the area and scroll. Screend stitches the frames into one tall image, keeping sticky headers and footers only once.

![Scrolling capture in action: an outlined area scrolls while the panel counts captured pixels, then the stitched result appears in the card](docs/images/scrolling.gif)

### And more
- **Copy text from anything (OCR).** Select an area and its text is on your clipboard.
- **Pin to screen.** Keep a screenshot floating above everything as a reference.
- **Screen recording.** Record an area, window or screen, and export it as a GIF from the card.
- **History.** Your recent captures, a shortcut away, without filling your Pictures folder.
- **Tray icon** with every action, plus global shortcuts for each one.

| Pinned screenshot | Recent captures |
|---|---|
| ![A beautified screenshot pinned above the notes app](docs/images/pin.png) | ![The history window with a grid of recent captures](docs/images/history.png) |

## Install

### Ubuntu and Debian
Download `screend_<version>_all.deb` from the [latest release](https://github.com/jksnetwork/screend/releases/latest) and open it, or:

```bash
sudo apt install ./screend_*_all.deb
```

apt installs everything Screend needs, including text recognition. Needs Ubuntu 24.04 / Debian 13 or newer.

### Any other distro (one line, no root)

```bash
curl -fsSL https://raw.githubusercontent.com/jksnetwork/screend/main/install.sh | bash
```

This installs the latest release for your user. If something is missing (Python GTK bindings, libadwaita), it tells you the exact command for your distro. Run the same line again to update.

### From source

```bash
git clone https://github.com/jksnetwork/screend.git
cd screend
./install.sh
```

### First run

Open **Screend** from your app menu (the one-line installer opens it for you). A **welcome screen** sets up your keyboard shortcuts (and lets you change them), starts Screend at login, and shows which optional extras are installed. On GNOME, **log out and back in once** afterwards so the Shell extension loads. Until then everything works, just more slowly.

Optional extras, each unlocking a feature (the welcome screen shows the exact install command):

| For | Package |
|---|---|
| Copying text (OCR) | `tesseract-ocr` / `tesseract` |
| GIF export | `ffmpeg` |
| Fast capture on Sway / Hyprland | `grim` |
| Corner card and pins on KDE / Hyprland / Sway | `gtk4-layer-shell` |

To uninstall: `sudo apt remove screend`, or `~/.local/share/screend/install.sh --uninstall` for the per-user install. Your screenshots are left alone.

## Using it

| Shortcut | Action |
|---|---|
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>S</kbd> | Capture an area, window or screen |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>W</kbd> | Capture the focused window |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>D</kbd> | Capture the current screen |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>A</kbd> | Capture all screens |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>R</kbd> | Capture the previous area again |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>T</kbd> | Copy the text in an area |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>C</kbd> | Scrolling capture (press again to finish) |
| <kbd>Shift</kbd>+<kbd>Super</kbd>+<kbd>V</kbd> | Start / stop recording |

Everything is also in the tray menu and on the command line (`screend --help`).

📖 **The [user guide](docs/user-guide.md)** covers every feature, all the editor shortcuts, settings, and troubleshooting.

## Desktop support

| | GNOME | KDE Plasma | Hyprland / Sway | Others |
|---|:-:|:-:|:-:|:-:|
| Area, window and screen capture | ✅ instant | ✅ | ✅ | ✅ via portal |
| Window snapping in the selector | ✅ | — | ✅ | — |
| Scrolling capture | ✅ | ✅ with KWin fast capture | ✅ with `grim` | — |
| Card and pins on top, no focus stealing | ✅ | ✅ with layer-shell | ✅ with layer-shell | partly |
| Screen recording | ✅ | ✅ | ✅ | ✅ via portal |
| Global shortcuts | ✅ installer | ✅ offered at install | your compositor config | — |

GNOME 46 on Ubuntu 24.04 is where Screend is developed and tested. The KDE, Hyprland and Sway support is new and hasn't had much real-world use yet, so reports are very welcome.

## Contributing

Bug reports, testing on other desktops, and pull requests are all appreciated. [CONTRIBUTING.md](CONTRIBUTING.md) explains how the pieces fit together, how to run the tests, and how to work on the GNOME extension without logging out.

## License

Screend is free software: you can redistribute it and/or modify it under the terms of the [GNU General Public License](LICENSE), version 3 or (at your option) any later version.
