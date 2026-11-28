#!/bin/sh
# noVNC/websockify proxy for the Hyprland session (wayvnc). Loopback only;
# the panel reaches it and injects the shared credentials.
set -e
. "$(dirname "$0")/_env.sh"
exec websockify --web="${NOVNC_DIR:-/usr/share/novnc}" --web-auth \
  --auth-plugin=BasicHTTPAuth --auth-source="$PANEL_USER:$PANEL_PASSWORD" \
  "127.0.0.1:${WS_HYPR_PORT:-6101}" "127.0.0.1:${VNC_HYPR_PORT:-5900}"
