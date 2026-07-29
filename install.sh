#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INIT_DIR="$SCRIPT_DIR/init"
INSTALL_DIR=""
VALIDATE_ONLY=0
DRY_RUN=0
NON_INTERACTIVE=0
SKIP_DEPENDENCIES=0
SKIP_BUILD=0
SKIP_SERVICES=0
PRESERVE_RUNTIME_CONFIG=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --init-dir PATH       Initialization kit (default: ./init)
  --install-dir PATH    Override the platform installation root
  --validate-only       Validate the kit and exit
  --dry-run             Print the installation plan without writing
  --non-interactive     Fail instead of offering to create init/
  --skip-dependencies   Do not create the Python venv or install packages
  --skip-build          Reuse an already built Control Plane binary
  --skip-services       Do not install launchd/systemd service definitions
  --preserve-runtime-config
                        Keep the installed .env, config, prompts and data
  -h, --help            Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --init-dir) INIT_DIR=$2; shift 2 ;;
    --install-dir) INSTALL_DIR=$2; shift 2 ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    --skip-dependencies) SKIP_DEPENDENCIES=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-services) SKIP_SERVICES=1; shift ;;
    --preserve-runtime-config) PRESERVE_RUNTIME_CONFIG=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$INIT_DIR" in
  /*) ;;
  *) INIT_DIR="$SCRIPT_DIR/$INIT_DIR" ;;
esac

if [ ! -d "$INIT_DIR" ]; then
  if [ "$NON_INTERACTIVE" -eq 1 ] || [ ! -t 0 ]; then
    echo "Initialization directory not found: $INIT_DIR" >&2
    echo "Run ./scripts/create-init.sh first." >&2
    exit 2
  fi
  printf "No init/ directory found. Create it from repository defaults? [Y/n] "
  read -r answer
  case "$answer" in
    n|N|no|NO) exit 1 ;;
    *) "$SCRIPT_DIR/scripts/create-init.sh" "$INIT_DIR" ;;
  esac
  echo "Edit $INIT_DIR/.env, then run the installer again."
  exit 0
fi

PYTHON_BIN=${PYTHON_BIN:-python3}
"$PYTHON_BIN" "$SCRIPT_DIR/scripts/validate-init.py" "$INIT_DIR"
"$SCRIPT_DIR/scripts/setup-ollama.sh" check "$INIT_DIR"

if [ "$VALIDATE_ONLY" -eq 1 ]; then
  exit 0
fi

OS_NAME=$(uname -s)
ARCH_NAME=$(uname -m)
case "$OS_NAME:$ARCH_NAME" in
  Darwin:arm64)
    PLATFORM=macos-arm64
    DEFAULT_ROOT="$HOME/Library/Application Support/HomeCortex"
    SERVICE_MANAGER=launchd
    ;;
  Linux:aarch64|Linux:arm64)
    PLATFORM=ventuno-arm64
    DEFAULT_ROOT=/opt/homecortex
    SERVICE_MANAGER=systemd
    ;;
  *)
    echo "Unsupported platform: $OS_NAME $ARCH_NAME" >&2
    exit 3
    ;;
esac

[ -n "$INSTALL_DIR" ] || INSTALL_DIR=$DEFAULT_ROOT

"$SCRIPT_DIR/scripts/platform-doctor.sh"

echo "HomeCortex installation plan"
echo "  Platform:       $PLATFORM"
echo "  Service manager:$SERVICE_MANAGER"
echo "  Source:         $SCRIPT_DIR"
echo "  Init kit:       $INIT_DIR"
echo "  Destination:    $INSTALL_DIR"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

if [ "$PLATFORM" = ventuno-arm64 ] && [ "$(id -u)" -ne 0 ]; then
  echo "The VENTUNO/Linux installation must run with sudo." >&2
  exit 4
fi

"$SCRIPT_DIR/scripts/setup-ollama.sh" ensure "$INIT_DIR"

backup_existing() {
  target=$1
  if [ -e "$target" ]; then
    stamp=$(date +%Y%m%d-%H%M%S)
    backup_dir="$INSTALL_DIR/backups/$stamp"
    mkdir -p "$backup_dir"
    cp -R "$target" "$backup_dir/"
  fi
}

mkdir -p "$INSTALL_DIR/app" "$INSTALL_DIR/bin" "$INSTALL_DIR/config" \
  "$INSTALL_DIR/prompts" "$INSTALL_DIR/data" "$INSTALL_DIR/models" \
  "$INSTALL_DIR/logs" "$INSTALL_DIR/runtime" "$INSTALL_DIR/backups" \
  "$INSTALL_DIR/HA"

if [ "$PRESERVE_RUNTIME_CONFIG" -eq 0 ]; then
  backup_existing "$INSTALL_DIR/config"
  cp -R "$INIT_DIR/config/." "$INSTALL_DIR/config/"
  cp -R "$INIT_DIR/prompts/." "$INSTALL_DIR/prompts/"
  if [ -d "$INIT_DIR/data" ]; then
    for data_file in "$INIT_DIR"/data/*; do
      [ -f "$data_file" ] || continue
      destination="$INSTALL_DIR/data/$(basename "$data_file")"
      backup_existing "$destination"
      cp "$data_file" "$destination"
      chmod 600 "$destination"
    done
  fi
  for prompt in "$INIT_DIR"/prompts/*.txt; do
    cp "$prompt" "$INSTALL_DIR/$(basename "$prompt")"
  done
  cp "$INIT_DIR/.env" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
else
  echo "Preserving installed configuration, prompts, secrets and data."
fi

for path in server.py backends services; do
  if [ -e "$SCRIPT_DIR/$path" ]; then
    cp -R "$SCRIPT_DIR/$path" "$INSTALL_DIR/app/"
  fi
done

if [ -d "$SCRIPT_DIR/HA" ]; then
  cp -R "$SCRIPT_DIR/HA/." "$INSTALL_DIR/HA/"
fi

if [ -d "$SCRIPT_DIR/models" ]; then
  cp -R "$SCRIPT_DIR/models/." "$INSTALL_DIR/models/"
fi

if [ "$SKIP_DEPENDENCIES" -eq 0 ]; then
  case "$PLATFORM" in
    macos-arm64) LOCK_FILE="$SCRIPT_DIR/requirements/macos-arm64-py314.lock" ;;
    *) LOCK_FILE="$SCRIPT_DIR/requirements/ventuno-arm64-py314.lock" ;;
  esac
  if [ ! -f "$LOCK_FILE" ]; then
    echo "Dependency lock not available for $PLATFORM: $LOCK_FILE" >&2
    exit 5
  fi
  PYTHON_RUNTIME=${PYTHON_RUNTIME:-python3.14}
  "$PYTHON_RUNTIME" -m venv "$INSTALL_DIR/.venv"
  "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$INSTALL_DIR/.venv/bin/python" -m pip install -r "$LOCK_FILE"
fi

if [ "$SKIP_BUILD" -eq 0 ]; then
  "$SCRIPT_DIR/scripts/build-control-plane.sh"
fi

if [ -x "$SCRIPT_DIR/control-plane/homecortex-control" ]; then
  cp "$SCRIPT_DIR/control-plane/homecortex-control" "$INSTALL_DIR/bin/"
else
  echo "Control Plane binary is missing." >&2
  exit 6
fi
cp "$SCRIPT_DIR/scripts/homecortex-maintenance.sh" "$INSTALL_DIR/bin/homecortex-maintenance"
chmod 755 "$INSTALL_DIR/bin/homecortex-maintenance"

if [ "$SKIP_SERVICES" -eq 0 ]; then
  if [ "$PLATFORM" = macos-arm64 ]; then
    "$SCRIPT_DIR/deploy/macos/install-launchagents.sh" "$INSTALL_DIR"
    START_AFTER_INSTALL=$(awk '/^[[:space:]]+start_after_install:/ {print $2; exit}' "$INIT_DIR/install.yaml")
    if [ "$START_AFTER_INSTALL" = true ]; then
      USER_DOMAIN="gui/$(id -u)"
      for label in io.homecortex.core io.homecortex.control; do
        plist="$HOME/Library/LaunchAgents/$label.plist"
        [ -f "$plist" ] || continue
        launchctl bootout "$USER_DOMAIN/$label" >/dev/null 2>&1 || true
        started=0
        attempt=1
        while [ "$attempt" -le 10 ]; do
          if launchctl bootstrap "$USER_DOMAIN" "$plist" >/dev/null 2>&1; then
            started=1
            break
          fi
          sleep 1
          attempt=$((attempt + 1))
        done
        if [ "$started" -ne 1 ]; then
          echo "Unable to start LaunchAgent after update: $label" >&2
          echo "Try: launchctl bootstrap $USER_DOMAIN \"$plist\"" >&2
          exit 7
        fi
      done
      echo "HomeCortex LaunchAgents started."
    fi
  else
    "$SCRIPT_DIR/deploy/linux/install-systemd.sh" "$INSTALL_DIR"
    START_AFTER_INSTALL=$(awk '/^[[:space:]]+start_after_install:/ {print $2; exit}' "$INIT_DIR/install.yaml")
    if [ "$START_AFTER_INSTALL" = true ]; then
      systemctl enable --now homecortex-core homecortex-control
      echo "HomeCortex systemd services started."
    fi
  fi
fi

echo "HomeCortex installed in: $INSTALL_DIR"
