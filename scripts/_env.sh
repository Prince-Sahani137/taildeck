# Sourced by the run scripts: loads ../.env into the environment.
DIR=$(cd "$(dirname "$0")/.." && pwd)
if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/.env"
  set +a
fi
: "${PANEL_USER:=admin}"
case "${PANEL_PASSWORD:-}" in
  ""|CHANGE_ME) echo "taildeck: set PANEL_PASSWORD in $DIR/.env" >&2; exit 1 ;;
esac
