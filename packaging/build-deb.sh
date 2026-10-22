#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/screend_<version>_all.deb (Ubuntu 24.04+, Debian 13+).
#
#   packaging/build-deb.sh
#
# Installs system-wide: the app in /usr/lib/screend, the launcher in /usr/bin,
# the GNOME extension, a systemd user unit (enabled per user from the welcome
# screen) and AppStream metadata so software centres show a proper page.
set -euo pipefail

src="$(cd "$(dirname "$0")/.." && pwd)"
version=$(python3 -c "import re,sys; print(re.search(r'\"(.+)\"', open(sys.argv[1]).read()).group(1))" \
  "$src/screend/__init__.py")
app_id=io.github.jksnetwork.Screend
ext_uuid=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['uuid'])" "$src/gnome-extension/metadata.json")

out="$src/dist"
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$out"

# -- files ----------------------------------------------------------------------
install -d "$root/usr/lib/screend"
cp -r "$src/screend" "$root/usr/lib/screend/"
find "$root/usr/lib/screend" -name __pycache__ -prune -exec rm -rf {} +
install -Dm755 "$src/bin/screend" "$root/usr/bin/screend"

install -Dm644 "$src/data/$app_id.desktop" "$root/usr/share/applications/$app_id.desktop"
install -Dm644 "$src/data/$app_id.metainfo.xml" "$root/usr/share/metainfo/$app_id.metainfo.xml"

ext="$root/usr/share/gnome-shell/extensions/$ext_uuid"
install -d "$ext"
install -m644 "$src/gnome-extension/"{metadata.json,extension.js,stylesheet.css} "$ext/"

install -Dm644 /dev/stdin "$root/usr/lib/systemd/user/screend.service" <<'UNIT'
[Unit]
Description=Screend screenshot service
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=/usr/bin/screend daemon
Restart=on-failure

[Install]
WantedBy=graphical-session.target
UNIT

doc="$root/usr/share/doc/screend"
install -d "$doc"
install -m644 "$src/README.md" "$doc/"
cat > "$doc/copyright" <<COPYRIGHT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Screend
Source: https://github.com/jksnetwork/screend

Files: *
Copyright: $(date +%Y) jksnetwork and Screend contributors
License: GPL-3.0-or-later
 On Debian systems, the full text of the GNU General Public License version 3
 can be found in /usr/share/common-licenses/GPL-3.
COPYRIGHT
printf 'screend (%s) unstable; urgency=medium\n\n  * Release %s.\n\n -- jksnetwork <4691887+jksnetwork@users.noreply.github.com>  %s\n' \
  "$version" "$version" "$(date -R)" | gzip -9n > "$doc/changelog.gz"

# -- control --------------------------------------------------------------------
install -d "$root/DEBIAN"
size=$(du -sk --exclude=DEBIAN "$root" | cut -f1)
cat > "$root/DEBIAN/control" <<CONTROL
Package: screend
Version: $version
Architecture: all
Maintainer: jksnetwork <4691887+jksnetwork@users.noreply.github.com>
Installed-Size: $size
Section: graphics
Priority: optional
Homepage: https://github.com/jksnetwork/screend
Depends: python3 (>= 3.10), python3-gi (>= 3.42), python3-gi-cairo, python3-cairo, python3-pil,
 gir1.2-gtk-4.0 (>= 4.12), gir1.2-adw-1 (>= 1.5), gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0,
 gstreamer1.0-pipewire, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, xdg-desktop-portal
Recommends: tesseract-ocr, ffmpeg
Suggests: grim
Description: screenshots, annotation and screen recording for Wayland
 Press a shortcut, drag over what you want, and a small card appears: copy it,
 drag it into a chat, mark it up, pin it above your windows, or copy the text
 out of it (OCR). Includes scrolling capture, presentable backgrounds and
 screen recording with GIF export.
 .
 On GNOME a bundled Shell extension makes capture instant. KDE Plasma,
 Hyprland and Sway are supported as well.
CONTROL

# checksums, for dpkg --verify
(cd "$root" && find . -path ./DEBIAN -prune -o -type f -printf '%P\0' | sort -z | xargs -0 md5sum) \
  > "$root/DEBIAN/md5sums"

deb="$out/screend_${version}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$root" "$deb" >/dev/null
echo "$deb"
