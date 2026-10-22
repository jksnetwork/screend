#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install Screend for the current user (no root needed).
#
#   curl -fsSL https://raw.githubusercontent.com/jksnetwork/screend/main/install.sh | bash
#   ./install.sh                      from a checkout
#
# Options:
#   --update            same as installing again; your settings are kept
#   --main              install the latest code from main instead of the latest release
#   --keybindings       also add the default GNOME shortcuts now (the welcome screen offers this)
#   --kde-fast-capture  let KWin hand screenshots to Screend directly (KDE only; see the user guide)
#   --uninstall         remove everything this script added (your screenshots stay)
set -euo pipefail

REPO=jksnetwork/screend
APP_ID=io.github.jksnetwork.Screend

# Started from inside a snap (e.g. VS Code's terminal)? Its GTK environment would crash
# the Python checks below, so shed it (the launcher does the same).
if [[ "${GSETTINGS_SCHEMA_DIR:-}${GTK_PATH:-}" == */snap/* ]]; then
  unset GSETTINGS_SCHEMA_DIR GTK_EXE_PREFIX GTK_PATH GTK_IM_MODULE_FILE GTK_MODULES GIO_MODULE_DIR LOCPATH
  unset GDK_PIXBUF_MODULE_FILE GDK_PIXBUF_MODULEDIR
  export XDG_DATA_DIRS="${XDG_DATA_DIRS_VSCODE_SNAP_ORIG:-/usr/local/share:/usr/share}"
  export XDG_CONFIG_DIRS="${XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG:-/etc/xdg}"
fi

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# -- run from a download when piped from curl ---------------------------------------

src="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
if [ ! -f "$src/screend/app.py" ]; then
  [[ " $* " == *" --uninstall "* ]] && [ -x "${XDG_DATA_HOME:-$HOME/.local/share}/screend/install.sh" ] \
    && exec "${XDG_DATA_HOME:-$HOME/.local/share}/screend/install.sh" "$@"
  command -v curl >/dev/null || die "curl is needed to download Screend."
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  url="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
  if [[ " $* " != *" --main "* ]]; then
    tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
          | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1 || true)
    [ -n "$tag" ] && url="https://github.com/$REPO/archive/refs/tags/$tag.tar.gz"
  fi
  say "Downloading Screend ${tag:-(main)}…"
  curl -fsSL "$url" | tar -xz -C "$tmp" || die "download failed: $url"
  bash "$tmp"/*/install.sh "$@"
  exit $?
fi

data="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data/screend"
bin_dir="$HOME/.local/bin"
desktop="$data/applications/$APP_ID.desktop"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/screend.service"
kwin_desktop="$data/applications/$APP_ID.kwin.desktop"
ext_uuid=$(sed -n 's/.*"uuid": *"\([^"]*\)".*/\1/p' "$src/gnome-extension/metadata.json")
ext_dir="$data/gnome-shell/extensions/$ext_uuid"

has() { [[ " $ARGS " == *" $1 "* ]]; }
ARGS="$*"

# -- helpers ----------------------------------------------------------------------------

package_manager() {
  local family=""
  [ -r /etc/os-release ] && family=$(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}")
  case "$family" in
    *debian*|*ubuntu*) echo apt ;;
    *fedora*|*rhel*) echo dnf ;;
    *arch*) echo pacman ;;
    *suse*) echo zypper ;;
  esac
}

check_dependencies() {
  local missing
  missing=$(python3 - <<'PY' 2>/dev/null || echo "python3"
import importlib
missing = []
try:
    import gi
    try:
        gi.require_version("Gtk", "4.0"); from gi.repository import Gtk  # noqa: F401
    except (ValueError, ImportError):
        missing.append("gtk4")
    try:
        gi.require_version("Adw", "1"); from gi.repository import Adw
        if (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) < (1, 5):
            missing.append("libadwaita>=1.5")
    except (ValueError, ImportError):
        missing.append("libadwaita")
except ImportError:
    missing.append("pygobject")
for module, name in (("cairo", "pycairo"), ("PIL", "pillow")):
    try:
        importlib.import_module(module)
    except ImportError:
        missing.append(name)
print(" ".join(missing))
PY
)
  [ -z "$missing" ] && return 0
  if [ "$missing" = "python3" ]; then
    command -v python3 >/dev/null && missing="a working PyGObject/GTK (python3 crashed importing it)"
  fi
  echo "Screend needs a few things that aren't installed: $missing"
  case "$(package_manager)" in
    apt) echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil" ;;
    dnf) echo "  sudo dnf install python3-gobject gtk4 libadwaita python3-pillow" ;;
    pacman) echo "  sudo pacman -S python-gobject python-cairo gtk4 libadwaita python-pillow" ;;
    zypper) echo "  sudo zypper install python3-gobject python3-gobject-cairo typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 python3-Pillow" ;;
    *) echo "  Install PyGObject, GTK 4, libadwaita (1.5 or newer), pycairo and Pillow with your package manager." ;;
  esac
  [[ "$missing" == *"libadwaita>=1.5"* ]] && echo "  (libadwaita 1.5 ships with Ubuntu 24.04, Fedora 40 and newer.)"
  echo "Then run this installer again."
  exit 1
}

# enabled-extensions is edited directly: GNOME only discovers a newly installed
# extension at the next login, and gnome-extensions refuses to enable it before then
ext_set_enabled() {
  command -v gsettings >/dev/null || return 0
  local list
  list=$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null | sed "s/@as //") || return 0
  list=$(python3 -c "import ast,sys; l=[u for u in ast.literal_eval(sys.argv[1]) if u!=sys.argv[2]]; l+= [sys.argv[2]] if sys.argv[3]=='1' else []; print(l)" "$list" "$ext_uuid" "$1")
  gsettings set org.gnome.shell enabled-extensions "$list"
}

shortcuts() {  # install | remove | list, using the installed copy
  SCREEND_LAUNCHER="$bin_dir/screend" PYTHONPATH="$app_dir" python3 -m screend.shortcuts "$1"
}

stop_running() {
  systemctl --user stop screend.service 2>/dev/null || true
  if pgrep -f "python3 -m screend" >/dev/null; then
    "$bin_dir/screend" quit 2>/dev/null || true
  fi
}

# -- uninstall --------------------------------------------------------------------------

if has --uninstall; then
  systemctl --user disable screend.service 2>/dev/null || true
  stop_running
  [ -d "$app_dir/screend" ] && shortcuts remove >/dev/null 2>&1 || true
  ext_set_enabled 0
  rm -rf "$app_dir" "$bin_dir/screend" "$desktop" "$unit" "$ext_dir" "$kwin_desktop"
  systemctl --user daemon-reload 2>/dev/null || true
  say "Screend removed. Your screenshots and settings were left in place."
  exit 0
fi

# -- install ----------------------------------------------------------------------------

check_dependencies
if [ -d /usr/lib/screend/screend ]; then
  echo "Note: the screend system package is also installed; this per-user copy takes precedence."
fi

say "Installing Screend to $app_dir"
stop_running
rm -rf "$app_dir"
mkdir -p "$app_dir" "$bin_dir" "$(dirname "$desktop")" "$(dirname "$unit")"
cp -r "$src/screend" "$app_dir/"
cp "$src/install.sh" "$app_dir/install.sh"   # kept for --uninstall
find "$app_dir" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 "$src/bin/screend" "$bin_dir/screend"

sed "s|Exec=screend|Exec=$bin_dir/screend|" "$src/data/$APP_ID.desktop" > "$desktop"
update-desktop-database "$(dirname "$desktop")" 2>/dev/null || true

if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
  say "Installing the GNOME Shell extension"
  rm -rf "$ext_dir"
  mkdir -p "$ext_dir"
  cp "$src/gnome-extension/"{metadata.json,extension.js,stylesheet.css} "$ext_dir/"
  ext_set_enabled 1
fi

# Start with the desktop session. A systemd user unit rather than XDG autostart,
# which some managed machines lock down.
cat > "$unit" <<UNIT
[Unit]
Description=Screend screenshot service
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=$bin_dir/screend daemon
Restart=on-failure

[Install]
WantedBy=graphical-session.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now screend.service >/dev/null 2>&1 || true

if has --keybindings; then
  say "Shortcuts:"
  shortcuts install
fi

if has --kde-fast-capture; then
  # KWin only serves ScreenShot2 to programs whose desktop file asks for it, matched by
  # executable path. Screend runs on python3, so this grants that interpreter.
  py=$(readlink -f "$(command -v python3)")
  cat > "$kwin_desktop" <<KWIN
[Desktop Entry]
Type=Application
Name=Screend (KWin capture)
Exec=$py -m screend
NoDisplay=true
X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2
KWIN
  echo "Granted KWin screenshot access to $py (log out and back in to apply)."
fi

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "Note: $bin_dir isn't on your PATH; add it to run 'screend' from a terminal." ;;
esac

say "Done."
if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] && ! gdbus call --session --dest "$APP_ID.Shell" \
    --object-path /io/github/jksnetwork/Screend/Shell --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
  echo "Log out and back in once so GNOME loads the Screend extension."
fi
config="${XDG_CONFIG_HOME:-$HOME/.config}/screend/config.json"
if grep -q '"welcomed": true' "$config" 2>/dev/null; then
  echo "Screend is running. Your settings and shortcuts were kept."
else
  echo "Screend is running. The welcome screen will help you set up shortcuts."
fi
