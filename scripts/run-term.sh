#!/bin/sh
# Browser terminal (ttyd). Loopback only, same credentials as the panel.
set -e
. "$(dirname "$0")/_env.sh"
exec ttyd -W -i 127.0.0.1 -p "${TERM_PORT:-6103}" \
  -c "$PANEL_USER:$PANEL_PASSWORD" "${TERM_SHELL:-${SHELL:-/bin/bash}}"
