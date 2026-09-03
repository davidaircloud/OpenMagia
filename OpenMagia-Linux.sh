#!/usr/bin/env bash
# Double-click/terminal launcher for Linux and NVIDIA workstations.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PORT="$(python3 -c 'import json; print(json.load(open("config.json")).get("port",8730))' 2>/dev/null || echo 8730)"
URL="http://127.0.0.1:$PORT"

echo "Starting the current OpenMagia version on $URL"
./start.sh --stop
EXPECTED_VERSION="$(tr -d '[:space:]' < VERSION)"
(
  for _ in $(seq 1 80); do
    running_version="$(curl -fsS "$URL/api/state" 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("engine") or {}).get("app_version") or "")' 2>/dev/null || true)"
    if [[ "$running_version" == "$EXPECTED_VERSION" ]]; then
      if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 &
      elif command -v gio >/dev/null 2>&1; then gio open "$URL" >/dev/null 2>&1 &
      else echo "Open $URL in your browser."
      fi
      exit 0
    fi
    sleep 0.25
  done
  echo "OpenMagia did not become ready. Check this terminal for the server error."
) &
echo "Keep this terminal open while using OpenMagia. Close it to stop the server."
exec ./start.sh
