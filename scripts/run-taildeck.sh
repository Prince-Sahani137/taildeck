#!/bin/sh
# Starts the panel. Python comes from ./.venv when present.
set -e
. "$(dirname "$0")/_env.sh"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$DIR/taildeck.py"
