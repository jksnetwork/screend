#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Run a command inside a throwaway nested GNOME Shell (in a window on your desktop).
#
#   tools/nested-shell.sh python3 tools/docs_screenshots.py
#
# The nested session has its own D-Bus bus and in-memory settings, so nothing it
# does touches your real session. It loads the Screend extension from
# ~/.local/share/gnome-shell/extensions (run ./install.sh first, or copy
# gnome-extension/ there). It uses your distro's session mode and an empty
# config/Desktop folder, so no personal files show up in it.
set -euo pipefail

if [ -z "${SCREEND_NESTED_BUS:-}" ]; then
  export SCREEND_NESTED_BUS=1
  exec dbus-run-session -- "$0" "$@"
fi

display=screend-nested-$$
log="${TMPDIR:-/tmp}/screend-nested-$$.log"
export GSETTINGS_BACKEND=memory
export MUTTER_DEBUG_DUMMY_MODE_SPECS="${SCREEND_NESTED_SIZE:-1600x1000}"
# private config dir with an empty Desktop, so desktop-icon extensions show nothing personal
private=$(mktemp -d "${TMPDIR:-/tmp}/screend-nested-XXXXXX")
mkdir -p "$private/config" "$private/Desktop"
echo "XDG_DESKTOP_DIR=\"$private/Desktop\"" > "$private/config/user-dirs.dirs"
export XDG_CONFIG_HOME="$private/config"
# GTK apps inside a bare session: no accessibility bus, software rendering
export GTK_A11Y=none NO_AT_BRIDGE=1 GSK_RENDERER=cairo

gnome-shell --nested --wayland --no-x11 --wayland-display="$display" >"$log" 2>&1 &
shell_pid=$!
trap 'kill $shell_pid 2>/dev/null || true; rm -rf "$private"' EXIT

shell_call() { gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell "$@"; }
# wait until the shell has exported its Extensions interface (owning the bus name isn't enough)
for _ in $(seq 120); do
  gdbus introspect --session --dest org.gnome.Shell --object-path /org/gnome/Shell 2>/dev/null \
    | grep -q org.gnome.Shell.Extensions && break
  sleep 0.25
done

# GtkApplication blocks on the portal at startup; activation is slow in a bare session
(gdbus introspect --session --dest org.freedesktop.portal.Desktop --object-path /org/freedesktop/portal/desktop >/dev/null 2>&1 &)
shell_call --method org.gnome.Shell.Extensions.EnableExtension screend@jksnetwork.github.io >/dev/null
for _ in $(seq 60); do
  gdbus introspect --session --dest org.freedesktop.portal.Desktop --object-path /org/freedesktop/portal/desktop >/dev/null 2>&1 && break
  sleep 0.5
done

WAYLAND_DISPLAY="$display" "$@"
