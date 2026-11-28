#!/bin/sh
# noVNC/websockify proxy for the XFCE (TigerVNC) session. Loopback only.
set -e
. "$(dirname "$0")/_env.sh"
exec websockify --web="${NOVNC_DIR:-/usr/share/novnc}" --web-auth \
  --auth-plugin=BasicHTTPAuth --auth-source="$PANEL_USER:$PANEL_PASSWORD" \
  "127.0.0.1:${WS_XFCE_PORT:-6102}" "127.0.0.1:${VNC_XFCE_PORT:-5902}"
