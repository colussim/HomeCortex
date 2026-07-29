#!/bin/sh
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/homecortex-lifecycle.XXXXXX")
INIT_DIR="$TEST_ROOT/init"
RUNTIME_DIR="$TEST_ROOT/runtime/HomeCortex"
CONTROL_URL="http://127.0.0.1:43219"
CONTROL_PID=""

cleanup() {
  if [ -n "$CONTROL_PID" ]; then
    kill "$CONTROL_PID" >/dev/null 2>&1 || true
    wait "$CONTROL_PID" >/dev/null 2>&1 || true
  fi
  case "$TEST_ROOT" in
    "${TMPDIR:-/tmp}"/homecortex-lifecycle.*) rm -rf "$TEST_ROOT" ;;
    *) echo "Refusing unsafe test cleanup: $TEST_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

assert_file() {
  [ -f "$1" ] || { echo "ASSERTION FAILED: missing file $1" >&2; exit 1; }
}

assert_absent() {
  [ ! -e "$1" ] || { echo "ASSERTION FAILED: path still exists $1" >&2; exit 1; }
}

echo "[1/7] Preparing an isolated initialization kit"
"$REPO_DIR/scripts/create-init.sh" "$INIT_DIR" >/dev/null
sed -i.bak \
  -e 's/change-me/integration-test-token/g' \
  -e 's|ELEVENLABS_API_KEY=""|ELEVENLABS_API_KEY="integration-test-key"|' \
  "$INIT_DIR/.env"
rm -f "$INIT_DIR/.env.bak"

echo "[2/7] Installing without host services or dependencies"
"$REPO_DIR/install.sh" \
  --init-dir "$INIT_DIR" \
  --install-dir "$RUNTIME_DIR" \
  --non-interactive \
  --skip-dependencies \
  --skip-build \
  --skip-services >/dev/null
assert_file "$RUNTIME_DIR/app/server.py"
assert_file "$RUNTIME_DIR/bin/homecortex-control"
assert_file "$RUNTIME_DIR/bin/homecortex-uninstall"

echo "[3/7] Starting an isolated Control Plane"
HOME_CORTEX_DISABLE_SERVICE_MANAGEMENT=1 \
  "$RUNTIME_DIR/bin/homecortex-control" \
  --root "$RUNTIME_DIR" \
  --addr 127.0.0.1:43219 \
  --core-url http://127.0.0.1:9 \
  >"$TEST_ROOT/control.log" 2>&1 &
CONTROL_PID=$!
ready=0
attempt=1
while [ "$attempt" -le 30 ]; do
  if curl -fsS "$CONTROL_URL/api/v1/backups" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
  attempt=$((attempt + 1))
done
[ "$ready" -eq 1 ] || { echo "Control Plane did not start" >&2; exit 1; }

printf 'preserved-runtime-prompt\n' > "$RUNTIME_DIR/prompts/prompt_fr.txt"

echo "[4/7] Updating while preserving runtime configuration"
HOMECORTEX_CONTROL_URL="$CONTROL_URL" \
  "$REPO_DIR/update.sh" \
  --init-dir "$INIT_DIR" \
  --install-dir "$RUNTIME_DIR" \
  --skip-dependencies \
  --skip-build \
  --skip-services >/dev/null
grep -Fq 'preserved-runtime-prompt' "$RUNTIME_DIR/prompts/prompt_fr.txt"

echo "[5/7] Creating and restoring a rollback point"
BACKUP_JSON=$(HOMECORTEX_CONTROL_URL="$CONTROL_URL" \
  "$RUNTIME_DIR/bin/homecortex-maintenance" backup)
BACKUP_NAME=$(printf '%s' "$BACKUP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
printf 'changed-after-backup\n' > "$RUNTIME_DIR/prompts/prompt_fr.txt"
HOMECORTEX_CONTROL_URL="$CONTROL_URL" \
  "$RUNTIME_DIR/bin/homecortex-maintenance" restore "$BACKUP_NAME" --yes >/dev/null
grep -Fq 'preserved-runtime-prompt' "$RUNTIME_DIR/prompts/prompt_fr.txt"

kill "$CONTROL_PID" >/dev/null 2>&1 || true
wait "$CONTROL_PID" >/dev/null 2>&1 || true
CONTROL_PID=""

echo "[6/7] Uninstalling while keeping local data"
"$REPO_DIR/uninstall.sh" --install-dir "$RUNTIME_DIR" --keep-data --yes >/dev/null
assert_absent "$RUNTIME_DIR/app"
assert_absent "$RUNTIME_DIR/bin"
assert_file "$RUNTIME_DIR/.env"
assert_file "$RUNTIME_DIR/prompts/prompt_fr.txt"
assert_file "$RUNTIME_DIR/backups/manual/$BACKUP_NAME"

echo "[7/7] Reinstalling, then testing explicitly confirmed purge"
"$REPO_DIR/install.sh" \
  --init-dir "$INIT_DIR" \
  --install-dir "$RUNTIME_DIR" \
  --non-interactive \
  --skip-dependencies \
  --skip-build \
  --skip-services >/dev/null
"$REPO_DIR/uninstall.sh" --install-dir "$RUNTIME_DIR" --purge --yes >/dev/null
assert_absent "$RUNTIME_DIR"

echo "HomeCortex lifecycle integration test passed."
