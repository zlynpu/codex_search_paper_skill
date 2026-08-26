#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
"$PYTHON_BIN" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/install.py" "$@"
