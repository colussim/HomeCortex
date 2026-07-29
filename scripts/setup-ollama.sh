#!/bin/sh
set -eu

MODE=${1:-}
INIT_DIR=${2:-}

usage() {
  echo "Usage: setup-ollama.sh check|ensure INIT_DIR" >&2
  exit 2
}

[ "$MODE" = check ] || [ "$MODE" = ensure ] || usage
[ -d "$INIT_DIR" ] || usage

if ! command -v ollama >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: Ollama is required but is not installed.
Install Ollama from https://ollama.com/download, start it once, then run the
HomeCortex installer again. HomeCortex does not install Ollama automatically.
EOF
  exit 10
fi

OLLAMA_MODEL=$(awk '
  /^llm:[[:space:]]*$/ { in_llm=1; next }
  in_llm && /^[^[:space:]]/ { exit }
  in_llm && /^[[:space:]]+model:[[:space:]]*/ {
    sub(/^[[:space:]]+model:[[:space:]]*/, "")
    sub(/[[:space:]]+#.*$/, "")
    gsub(/^["'"'"']|["'"'"']$/, "")
    print
    exit
  }
' "$INIT_DIR/config/kira.yaml")

if [ -z "$OLLAMA_MODEL" ]; then
  echo "ERROR: llm.model is missing from $INIT_DIR/config/kira.yaml" >&2
  exit 11
fi
case "$OLLAMA_MODEL" in
  *[!A-Za-z0-9._:/-]*)
    echo "ERROR: llm.model contains unsupported characters: $OLLAMA_MODEL" >&2
    exit 11
    ;;
esac

echo "Ollama prerequisite"
echo "  Version:          $(ollama --version 2>/dev/null || echo detected)"
echo "  Required model:   $OLLAMA_MODEL"

[ "$MODE" = ensure ] || exit 0

ollama_online() {
  curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1
}

if ! ollama_online; then
  echo "  Server:           starting"
  case "$(uname -s)" in
    Darwin)
      open -gja Ollama >/dev/null 2>&1 || true
      ;;
    Linux)
      if command -v systemctl >/dev/null 2>&1; then
        systemctl start ollama >/dev/null 2>&1 || true
      fi
      ;;
  esac
  attempt=0
  while [ "$attempt" -lt 20 ] && ! ollama_online; do
    attempt=$((attempt + 1))
    sleep 1
  done
fi

if ! ollama_online; then
  echo "ERROR: Ollama is installed but its local server is unavailable on 127.0.0.1:11434." >&2
  echo "Start Ollama and run the HomeCortex installer again." >&2
  exit 12
fi
echo "  Server:           ready"

PULL_MODEL=$(awk '
  /^ollama:[[:space:]]*$/ { in_ollama=1; next }
  in_ollama && /^[^[:space:]]/ { exit }
  in_ollama && /^[[:space:]]+pull_model:[[:space:]]*/ {
    sub(/^[[:space:]]+pull_model:[[:space:]]*/, "")
    sub(/[[:space:]]+#.*$/, "")
    print
    exit
  }
' "$INIT_DIR/install.yaml")

if ollama list 2>/dev/null | awk -v wanted="$OLLAMA_MODEL" 'NR > 1 && $1 == wanted { found=1 } END { exit !found }'; then
  echo "  Model:            already installed"
elif [ "$PULL_MODEL" = true ]; then
  echo "  Model:            downloading $OLLAMA_MODEL"
  ollama pull "$OLLAMA_MODEL"
else
  echo "ERROR: required Ollama model is missing: $OLLAMA_MODEL" >&2
  echo "Set ollama.pull_model: true or run: ollama pull $OLLAMA_MODEL" >&2
  exit 13
fi
