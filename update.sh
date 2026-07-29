#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INIT_DIR="$SCRIPT_DIR/init"
INSTALL_DIR=""
APPLY_INIT=0
SKIP_DEPENDENCIES=0
SKIP_BUILD=0

usage() {
  cat <<'EOF'
Usage: ./update.sh [options]

Safely update an existing HomeCortex installation. A recovery backup is
created before any installed file is changed. Runtime configuration, prompts,
secrets and databases are preserved unless --apply-init is explicitly used.

  --init-dir PATH       Initialization kit (default: ./init)
  --install-dir PATH    Override the platform installation root
  --apply-init          Replace runtime configuration with the init kit
  --skip-dependencies   Keep the existing Python environment
  --skip-build          Reuse the existing Control Plane build
  -h, --help            Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --init-dir) INIT_DIR=$2; shift 2 ;;
    --install-dir) INSTALL_DIR=$2; shift 2 ;;
    --apply-init) APPLY_INIT=1; shift ;;
    --skip-dependencies) SKIP_DEPENDENCIES=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$INIT_DIR" in
  /*) ;;
  *) INIT_DIR="$SCRIPT_DIR/$INIT_DIR" ;;
esac

if [ -z "$INSTALL_DIR" ]; then
  case "$(uname -s):$(uname -m)" in
    Darwin:arm64) INSTALL_DIR="$HOME/Library/Application Support/HomeCortex" ;;
    Linux:aarch64|Linux:arm64) INSTALL_DIR=/opt/homecortex ;;
    *) echo "Unsupported platform: $(uname -s) $(uname -m)" >&2; exit 3 ;;
  esac
fi

if [ ! -x "$INSTALL_DIR/bin/homecortex-maintenance" ] || [ ! -f "$INSTALL_DIR/.env" ]; then
  echo "No existing HomeCortex installation found in: $INSTALL_DIR" >&2
  echo "Run ./install.sh first." >&2
  exit 4
fi

echo "Creating the mandatory pre-update recovery backup..."
"$INSTALL_DIR/bin/homecortex-maintenance" backup

set -- --init-dir "$INIT_DIR" --install-dir "$INSTALL_DIR" --non-interactive
[ "$APPLY_INIT" -eq 1 ] || set -- "$@" --preserve-runtime-config
[ "$SKIP_DEPENDENCIES" -eq 0 ] || set -- "$@" --skip-dependencies
[ "$SKIP_BUILD" -eq 0 ] || set -- "$@" --skip-build

echo "Updating HomeCortex..."
"$SCRIPT_DIR/install.sh" "$@"

echo "HomeCortex update completed successfully."
