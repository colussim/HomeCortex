#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
VERSION=${1:-}
REF=${2:-HEAD}

if [ -z "$VERSION" ]; then
  echo "Usage: ./scripts/package-release.sh VERSION [GIT_REF]" >&2
  echo "Example: ./scripts/package-release.sh 1.2.1 v1.2.1" >&2
  exit 2
fi

case "$VERSION" in
  *[!0-9.]*|"") echo "Invalid release version: $VERSION" >&2; exit 2 ;;
esac

cd "$REPO_DIR"

git rev-parse --verify "$REF^{commit}" >/dev/null
if [ "$(git rev-parse "$REF^{commit}")" != "$(git rev-parse HEAD)" ]; then
  echo "Release ref must point to the checked-out commit." >&2
  exit 3
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked files must be committed before packaging a release." >&2
  exit 3
fi

grep -Fq "\"version\":  \"${VERSION}\"" control-plane/main.go
grep -Fq "\"version\": \"${VERSION}\"" control-plane/web/package.json
grep -Fq "version = \"${VERSION}\"" pyproject.toml

"$SCRIPT_DIR/build-control-plane.sh"

PACKAGE_ROOT="HomeCortex-$VERSION"
OUTPUT_DIR="$REPO_DIR/dist"
ARCHIVE="$OUTPUT_DIR/HomeCortex-$VERSION-macos-arm64.tar.gz"
CHECKSUM="$ARCHIVE.sha256"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/homecortex-release.XXXXXX")

cleanup() {
  case "$TEMP_DIR" in
    "${TMPDIR:-/tmp}"/homecortex-release.*) rm -rf "$TEMP_DIR" ;;
    *) echo "Refusing unsafe release cleanup: $TEMP_DIR" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

mkdir -p "$TEMP_DIR/$PACKAGE_ROOT" "$OUTPUT_DIR"
git archive "$REF" | tar -x -C "$TEMP_DIR/$PACKAGE_ROOT"
cp control-plane/homecortex-control \
  "$TEMP_DIR/$PACKAGE_ROOT/control-plane/homecortex-control"
chmod 755 \
  "$TEMP_DIR/$PACKAGE_ROOT/control-plane/homecortex-control" \
  "$TEMP_DIR/$PACKAGE_ROOT/install.sh" \
  "$TEMP_DIR/$PACKAGE_ROOT/update.sh" \
  "$TEMP_DIR/$PACKAGE_ROOT/uninstall.sh"

if find "$TEMP_DIR/$PACKAGE_ROOT" \
  \( -name .env -o -name '*.db' -o -path '*/init/*' \) -print -quit |
  grep -q .; then
  echo "Release package contains a forbidden private file." >&2
  exit 4
fi
if grep -E '"token"[[:space:]]*:' \
  "$TEMP_DIR/$PACKAGE_ROOT/config/satellites.json" |
  grep -vq 'change-me'; then
  echo "Release satellite configuration contains a non-placeholder token." >&2
  exit 4
fi

tar -czf "$ARCHIVE" -C "$TEMP_DIR" "$PACKAGE_ROOT"
shasum -a 256 "$ARCHIVE" > "$CHECKSUM"

echo "Release archive: $ARCHIVE"
echo "SHA-256 file:   $CHECKSUM"
