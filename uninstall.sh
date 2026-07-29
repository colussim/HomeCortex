#!/bin/sh
set -eu

INSTALL_DIR=""
MODE=keep-data
ASSUME_YES=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: ./uninstall.sh [options]

  --install-dir PATH    Override the platform installation root
  --keep-data           Remove services and application files but keep local data
                        (default)
  --purge               Remove the complete HomeCortex runtime after confirmation
  --yes                 Skip the interactive confirmation
  --dry-run             Show exactly what would be removed
  -h, --help            Show this help

The keep-data mode preserves .env, config, prompts, data, models, HA, logs and
backups. Ollama, its models, Home Assistant and system Python are never removed.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir) INSTALL_DIR=$2; shift 2 ;;
    --keep-data) MODE=keep-data; shift ;;
    --purge) MODE=purge; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

OS_NAME=$(uname -s)
ARCH_NAME=$(uname -m)
if [ -z "$INSTALL_DIR" ]; then
  case "$OS_NAME:$ARCH_NAME" in
    Darwin:arm64) INSTALL_DIR="$HOME/Library/Application Support/HomeCortex" ;;
    Linux:aarch64|Linux:arm64) INSTALL_DIR=/opt/homecortex ;;
    *) echo "Unsupported platform: $OS_NAME $ARCH_NAME" >&2; exit 3 ;;
  esac
fi

case "$INSTALL_DIR" in
  /*) ;;
  *) echo "Installation path must be absolute: $INSTALL_DIR" >&2; exit 4 ;;
esac

[ ! -L "$INSTALL_DIR" ] || {
  echo "Refusing to uninstall through a symbolic-link installation root." >&2
  exit 4
}

case "$INSTALL_DIR" in
  /|/Applications|/Library|/System|/Users|/opt|/usr|/usr/local|/var|"$HOME")
    echo "Refusing unsafe installation root: $INSTALL_DIR" >&2
    exit 4
    ;;
esac

if [ ! -f "$INSTALL_DIR/.env" ] ||
   { [ ! -f "$INSTALL_DIR/app/server.py" ] && [ ! -d "$INSTALL_DIR/config" ]; }; then
  echo "No recognizable HomeCortex installation found in: $INSTALL_DIR" >&2
  exit 5
fi

echo "HomeCortex uninstallation plan"
echo "  Platform:    $OS_NAME $ARCH_NAME"
echo "  Installation:$INSTALL_DIR"
echo "  Mode:        $MODE"
if [ "$MODE" = keep-data ]; then
  echo "  Preserved:   .env, config, prompts, data, models, HA, logs, backups"
else
  echo "  Preserved:   nothing inside the HomeCortex runtime"
fi
echo "  External:    Ollama, Ollama models, Home Assistant and system Python untouched"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
  if [ "$MODE" = purge ]; then
    printf "Permanently remove the complete HomeCortex runtime? Type PURGE: "
    read -r answer
    [ "$answer" = PURGE ] || { echo "Cancelled."; exit 1; }
  else
    printf "Remove HomeCortex while preserving local data? [y/N] "
    read -r answer
    case "$answer" in y|Y|yes|YES) ;; *) echo "Cancelled."; exit 1 ;; esac
  fi
fi

if [ "$OS_NAME" = Darwin ]; then
  USER_DOMAIN="gui/$(id -u)"
  for label in io.homecortex.core io.homecortex.control; do
    plist="$HOME/Library/LaunchAgents/$label.plist"
    if [ -f "$plist" ] && grep -Fq "$INSTALL_DIR" "$plist"; then
      launchctl bootout "$USER_DOMAIN/$label" >/dev/null 2>&1 || true
      rm -f "$plist"
      echo "Removed LaunchAgent: $label"
    fi
  done
elif [ "$OS_NAME" = Linux ]; then
  [ "$(id -u)" -eq 0 ] || {
    echo "Linux uninstallation must run with sudo." >&2
    exit 6
  }
  for unit in homecortex-core.service homecortex-control.service; do
    unit_path="/etc/systemd/system/$unit"
    if [ -f "$unit_path" ] && grep -Fq "$INSTALL_DIR" "$unit_path"; then
      systemctl disable --now "${unit%.service}" >/dev/null 2>&1 || true
      rm -f "$unit_path"
      echo "Removed systemd unit: $unit"
    fi
  done
  systemctl daemon-reload
fi

if [ "$MODE" = purge ]; then
  rm -rf "$INSTALL_DIR"
  echo "HomeCortex runtime removed completely: $INSTALL_DIR"
else
  for path in app bin .venv runtime; do
    rm -rf "$INSTALL_DIR/$path"
  done
  echo "HomeCortex application removed; local data preserved in: $INSTALL_DIR"
fi
