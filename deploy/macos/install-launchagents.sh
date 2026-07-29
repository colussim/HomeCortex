#!/bin/sh
set -eu

INSTALL_DIR=$1
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AGENTS_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS_DIR"

escape_xml() {
  printf '%s' "$1" | sed 's/&/\\&amp;/g; s/</\\&lt;/g; s/>/\\&gt;/g'
}

ROOT_XML=$(escape_xml "$INSTALL_DIR")
if [ -x "$INSTALL_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$INSTALL_DIR/.venv/bin/python"
else
  PYTHON_BIN=${PYTHON_BIN:-python3}
fi
PYTHON_XML=$(escape_xml "$PYTHON_BIN")

sed \
  -e "s|__INSTALL_ROOT__|$ROOT_XML|g" \
  -e "s|__PYTHON_BIN__|$PYTHON_XML|g" \
  "$SCRIPT_DIR/io.homecortex.core.plist.tmpl" \
  > "$AGENTS_DIR/io.homecortex.core.plist"

if [ -x "$INSTALL_DIR/bin/homecortex-control" ]; then
  sed "s|__INSTALL_ROOT__|$ROOT_XML|g" \
    "$SCRIPT_DIR/io.homecortex.control.plist.tmpl" \
    > "$AGENTS_DIR/io.homecortex.control.plist"
fi

echo "LaunchAgents installed in $AGENTS_DIR"
