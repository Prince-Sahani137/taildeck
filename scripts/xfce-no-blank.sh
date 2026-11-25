#!/bin/sh
# Keep the virtual XFCE desktop usable for remote viewing: no screen blanking,
# no lock screen. Retries until the X server and session bus are up.
[ -f "$(dirname "$0")/../.env" ] && { set -a; . "$(dirname "$0")/../.env"; set +a; }
export DISPLAY="${XFCE_DISPLAY:-:2}"
export XAUTHORITY="${XFCE_XAUTHORITY:-$HOME/.Xauthority}"

i=0
while [ "$i" -lt 40 ]; do
  if xset q >/dev/null 2>&1; then
    xset s off s noblank >/dev/null 2>&1
    xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s false >/dev/null 2>&1
    xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s false >/dev/null 2>&1
    xfconf-query -c xfce4-screensaver -p /saver/idle-activation/enabled -n -t bool -s false >/dev/null 2>&1
    if xfconf-query -c xfce4-screensaver -p /saver/enabled >/dev/null 2>&1; then
      xfce4-screensaver-command -d >/dev/null 2>&1 || true
      exit 0
    fi
  fi
  i=$((i + 1))
  sleep 1
done
exit 0
