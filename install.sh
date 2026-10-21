#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install Screend for the current user.
#   ./install.sh               install app, launcher, menu entry and login service
#   ./install.sh --keybindings also bind hotkeys in GNOME (see KEYS below)
#   ./install.sh --kde-fast-capture  let KWin hand screenshots to Screend directly (KDE only; see README)
#   ./install.sh --uninstall   remove everything this script added
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data/screend"
bin_dir="$HOME/.local/bin"
desktop="$data/applications/io.github.jksnetwork.Screend.desktop"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/screend.service"
kwin_desktop="$data/applications/io.github.jksnetwork.Screend.kwin.desktop"
ext_uuid="screend@jksnetwork.github.io"
ext_dir="$data/gnome-shell/extensions/$ext_uuid"

kb_schema=org.gnome.settings-daemon.plugins.media-keys
kb_base=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
# name|command|binding
KEYS=(
  "Screend: capture area|screend region|<Shift><Super>s"
  "Screend: capture all screens|screend fullscreen|<Shift><Super>a"
  "Screend: capture previous area|screend last|<Shift><Super>r"
  "Screend: capture focused window|screend window|<Shift><Super>w"
  "Screend: capture current screen|screend screen|<Shift><Super>d"
  "Screend: start/stop recording|screend record|<Shift><Super>v"
  "Screend: copy text from area|screend text|<Shift><Super>t"
  "Screend: scrolling capture|screend scroll|<Shift><Super>c"
)

# enabled-extensions is edited directly: GNOME only discovers a newly installed
# extension at the next login, and gnome-extensions refuses to enable it before then
ext_set_enabled() {
  local list; list=$(gsettings get org.gnome.shell enabled-extensions | sed "s/@as //")
  list=$(python3 -c "import ast,sys; l=[u for u in ast.literal_eval(sys.argv[1]) if u!=sys.argv[2]]; l+= [sys.argv[2]] if sys.argv[3]=='1' else []; print(l)" "$list" "$ext_uuid" "$1")
  gsettings set org.gnome.shell enabled-extensions "$list"
}

kb_list() { gsettings get $kb_schema custom-keybindings | sed "s/@as //"; }

remove_keybindings() {
  local list; list=$(kb_list)
  for i in "${!KEYS[@]}"; do
    local path="$kb_base/screend$i/"
    list=$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1]); print([p for p in l if p!=sys.argv[2]])" "$list" "$path")
    dconf reset -f "$path" 2>/dev/null || true
  done
  gsettings set $kb_schema custom-keybindings "$list"
}

add_keybindings() {
  remove_keybindings
  local list; list=$(kb_list)
  for i in "${!KEYS[@]}"; do
    IFS='|' read -r name cmd binding <<<"${KEYS[$i]}"
    local path="$kb_base/screend$i/"
    gsettings set $kb_schema.custom-keybinding:"$path" name "$name"
    gsettings set $kb_schema.custom-keybinding:"$path" command "$bin_dir/$cmd"
    gsettings set $kb_schema.custom-keybinding:"$path" binding "$binding"
    list=$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1]); l.append(sys.argv[2]); print(l)" "$list" "$path")
    echo "  $binding -> $cmd"
  done
  gsettings set $kb_schema custom-keybindings "$list"
}

stop_running() {
  systemctl --user stop screend.service 2>/dev/null || true
  if pgrep -f "python3 -m screend" >/dev/null; then
    "$bin_dir/screend" quit 2>/dev/null || true
  fi
}

if [ "${1:-}" = "--uninstall" ]; then
  systemctl --user disable screend.service 2>/dev/null || true
  stop_running
  remove_keybindings
  ext_set_enabled 0
  rm -rf "$app_dir" "$bin_dir/screend" "$desktop" "$unit" "$ext_dir" "$kwin_desktop"
  systemctl --user daemon-reload
  echo "Screend removed (your screenshots were left in place)."
  exit 0
fi

echo "Installing Screend to $app_dir"
stop_running
rm -rf "$app_dir"
mkdir -p "$app_dir" "$bin_dir" "$(dirname "$desktop")" "$(dirname "$unit")"
cp -r "$src/screend" "$app_dir/"
find "$app_dir" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 "$src/bin/screend" "$bin_dir/screend"

sed "s|Exec=screend|Exec=$bin_dir/screend|" "$src/data/io.github.jksnetwork.Screend.desktop" > "$desktop"
update-desktop-database "$(dirname "$desktop")" 2>/dev/null || true

if [ "${XDG_CURRENT_DESKTOP:-}" != "${XDG_CURRENT_DESKTOP/GNOME/}" ]; then
  echo "Installing GNOME Shell helper extension"
  rm -rf "$ext_dir"
  mkdir -p "$ext_dir"
  cp "$src/gnome-extension/"{metadata.json,extension.js,stylesheet.css} "$ext_dir/"
  ext_set_enabled 1
  if ! gdbus call --session --dest io.github.jksnetwork.Screend.Shell --object-path /io/github/jksnetwork/Screend/Shell \
      --method org.freedesktop.DBus.Properties.Get io.github.jksnetwork.Screend.Shell Version >/dev/null 2>&1; then
    echo "  -> log out and back in once to activate it (Screend works without it meanwhile)"
  fi
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
systemctl --user enable --now screend.service

for arg in "$@"; do
  case "$arg" in
    --keybindings)
      if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
        echo "Adding GNOME keyboard shortcuts:"
        add_keybindings
      else
        echo "--keybindings only configures GNOME; see README for KDE, Sway and Hyprland."
      fi
      ;;
    --kde-fast-capture)
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
      ;;
  esac
done

case "${XDG_CURRENT_DESKTOP:-}" in
  *GNOME*|*KDE*) ;;
  *)
    if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
      echo "Hyprland: add to hyprland.conf, e.g.  bind = SUPER SHIFT, S, exec, $bin_dir/screend region"
    elif [ -n "${SWAYSOCK:-}" ]; then
      echo "Sway: add to your config, e.g.  bindsym \$mod+Shift+s exec $bin_dir/screend region"
    fi
    ;;
esac

echo "Done. Run 'screend' (or use your shortcut) to capture an area."
