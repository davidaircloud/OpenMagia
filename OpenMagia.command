#!/usr/bin/env bash
# OpenMagia launcher — double-click to run the version on disk.
cd "$(dirname "$0")" || exit 1

PORT=$(python3 -c "import json;print(json.load(open('config.json')).get('port',8730))" 2>/dev/null || echo 8730)
URL="http://localhost:$PORT"

echo "Starting the current OpenMagia version on $URL"
EXPECTED_VERSION="$(tr -d '[:space:]' < VERSION)"
running_version="$(curl -fsS "$URL/api/state" 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("engine") or {}).get("app_version") or "")' 2>/dev/null || true)"
if [[ -n "$EXPECTED_VERSION" && "$running_version" == "$EXPECTED_VERSION" ]]; then
  echo "OpenMagia $EXPECTED_VERSION is already running. Opening it now."
  open "$URL"
  exit 0
fi
if ! ./start.sh --stop; then
  echo
  echo "OpenMagia could not stop its previous server."
  read -r -p "Press Return to close…" _
  exit 1
fi

(
  for _ in $(seq 1 80); do
    running_version="$(curl -fsS "$URL/api/state" 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("engine") or {}).get("app_version") or "")' 2>/dev/null || true)"
    if [[ "$running_version" == "$EXPECTED_VERSION" ]]; then
      open "$URL"
      exit 0
    fi
    sleep 0.25
  done
  echo "OpenMagia did not become ready. Check this window for the server error."
) &

echo "Keep this window open while using OpenMagia. Close it to stop the server."
exec ./start.sh
