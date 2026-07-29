#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
WEB_DIR="$REPO_DIR/control-plane/web"
GO_DIR="$REPO_DIR/control-plane"

if [ ! -d "$WEB_DIR/node_modules" ]; then
  (cd "$WEB_DIR" && npm ci)
fi
(cd "$WEB_DIR" && npm run build)

GOCACHE=${GOCACHE:-/tmp/homecortex-go-cache}
export GOCACHE
(cd "$GO_DIR" && go build -trimpath -o homecortex-control .)

echo "Control Plane built: $GO_DIR/homecortex-control"

