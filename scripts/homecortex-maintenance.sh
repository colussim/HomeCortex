#!/bin/sh
set -eu

CONTROL_URL=${HOMECORTEX_CONTROL_URL:-http://127.0.0.1:3210}

usage() {
  cat <<'EOF'
Usage:
  homecortex-maintenance list
  homecortex-maintenance backup [--include-tts]
  homecortex-maintenance restore BACKUP_NAME [--yes]

Backups contain configuration, prompts, data and .env secrets. They are stored
locally with mode 0600 under the HomeCortex runtime backup directory.
EOF
}

command_name=${1:-}
[ -n "$command_name" ] || { usage; exit 2; }
shift

case "$command_name" in
  list)
    curl -fsS "$CONTROL_URL/api/v1/backups"
    printf "\n"
    ;;
  backup)
    include_tts=false
    if [ "${1:-}" = "--include-tts" ]; then
      include_tts=true
      shift
    fi
    [ "$#" -eq 0 ] || { usage >&2; exit 2; }
    curl -fsS -X POST "$CONTROL_URL/api/v1/backups" \
      -H "Content-Type: application/json" \
      -d "{\"include_tts_cache\":$include_tts}"
    printf "\n"
    ;;
  restore)
    backup_name=${1:-}
    [ -n "$backup_name" ] || { usage >&2; exit 2; }
    shift
    confirmed=false
    if [ "${1:-}" = "--yes" ]; then
      confirmed=true
      shift
    fi
    [ "$#" -eq 0 ] || { usage >&2; exit 2; }
    case "$backup_name" in
      homecortex-*.zip) ;;
      *) echo "Invalid backup name." >&2; exit 2 ;;
    esac
    if [ "$confirmed" != true ]; then
      printf "Restore %s and restart Kira? [y/N] " "$backup_name"
      read -r answer
      case "$answer" in y|Y|yes|YES) ;; *) exit 1 ;; esac
    fi
    curl -fsS -X POST "$CONTROL_URL/api/v1/backups/$backup_name/restore"
    printf "\n"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
