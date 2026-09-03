#!/usr/bin/env bash
# OpenMagia uninstaller.
#
# By default removes ONLY the OpenMagia folder (config, project, media,
# uploads). The h3.c checkout and the (large) model root are left untouched —
# they are shared resources that may be used by other tools.
#
#   ./remove.sh            remove OpenMagia only
#   ./remove.sh --model    also delete the model root from config.json
#   ./remove.sh --h3       also delete the h3.c checkout
#   ./remove.sh --all      remove OpenMagia + model + h3.c
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$SCRIPT_DIR/config.json"

MODE="openmagia"
for a in "$@"; do
  case "$a" in
    --model) MODE="model";;
    --h3)    MODE="h3";;
    --all)   MODE="all";;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $a"; exit 1;;
  esac
done

log() { printf '\033[1;32m[openmagia]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[openmagia]\033[0m %s\n' "$*" >&2; }

MODEL_ROOT=""; H3_BIN=""
if [[ -f "$CFG" ]]; then
  MODEL_ROOT="$(python3 -c "import json;print(json.load(open('$CFG')).get('model_root',''))" 2>/dev/null || true)"
  H3_BIN="$(python3 -c "import json;print(json.load(open('$CFG')).get('h3_bin',''))" 2>/dev/null || true)"
fi

H3C_DIR="$(dirname "${H3_BIN:-}")"

confirm() {
  echo
  echo "This will delete:"
  echo "  - $SCRIPT_DIR"
  [[ "$MODE" == "model" || "$MODE" == "all" ]] && [[ -n "$MODEL_ROOT" ]] && echo "  - $MODEL_ROOT  (model, ~$(du -sh "$MODEL_ROOT" 2>/dev/null | cut -f1))"
  [[ "$MODE" == "h3" || "$MODE" == "all" ]] && [[ -n "$H3C_DIR" ]] && echo "  - $H3C_DIR  (h3.c checkout)"
  read -r -p "Continue? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { log "aborted"; exit 0; }
}

confirm

# stop a running server
if [[ -f "$SCRIPT_DIR/server.pid" ]]; then
  kill "$(cat "$SCRIPT_DIR/server.pid")" 2>/dev/null || true
fi

rm -rf "$SCRIPT_DIR"
log "removed OpenMagia"

if [[ ( "$MODE" == "model" || "$MODE" == "all" ) && -n "$MODEL_ROOT" && -d "$MODEL_ROOT" ]]; then
  rm -rf "$MODEL_ROOT"
  log "removed model root $MODEL_ROOT"
fi

if [[ ( "$MODE" == "h3" || "$MODE" == "all" ) && -n "$H3C_DIR" && -d "$H3C_DIR" ]]; then
  rm -rf "$H3C_DIR"
  log "removed h3.c checkout $H3C_DIR"
fi

log "done."
