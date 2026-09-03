#!/usr/bin/env bash
# Start the OpenMagia server.
set -euo pipefail
# launchd supplies only the system path. Include the standard Homebrew paths
# used by the app's Python runtime before resolving python3.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$SCRIPT_DIR/addons/ffmpeg/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$SCRIPT_DIR"
RUNTIME_DIR="$SCRIPT_DIR/data/runtime"
PID_FILE="$RUNTIME_DIR/server.pid"
LOG_FILE="$RUNTIME_DIR/server.log"
LAUNCH_LABEL="com.openmagia.server"
PORT="$(python3 -c "
import json,os
try:
    print(json.load(open('config.json')).get('port', 8730))
except Exception:
    print(os.environ.get('OPENMAGIA_PORT', 8730))
")"
EXPECTED_VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION" 2>/dev/null || true)"

server_is_ready() {
  python3 - "$PORT" <<'PY'
import socket,sys
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=.25):
        pass
except OSError:
    raise SystemExit(1)
PY
}

server_is_current() {
  [[ -n "$EXPECTED_VERSION" ]] || return 1
  current="$(curl -fsS "http://127.0.0.1:$PORT/api/state" 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("engine") or {}).get("app_version") or "")' 2>/dev/null || true)"
  [[ "$current" == "$EXPECTED_VERSION" ]]
}

server_is_openmagia() {
  curl -fsS "http://127.0.0.1:$PORT/api/state" 2>/dev/null | python3 -c '
import json,sys
try:
    state=json.load(sys.stdin)
    raise SystemExit(0 if isinstance(state.get("engine"), dict) else 1)
except Exception:
    raise SystemExit(1)
' >/dev/null 2>&1
}

stop_managed_server() {
  stopped=0
  if [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1 && launchctl print "gui/$(id -u)/$LAUNCH_LABEL" >/dev/null 2>&1; then
    launchctl remove "$LAUNCH_LABEL" >/dev/null 2>&1 || true
    stopped=1
  fi
  if [[ -f "$PID_FILE" ]]; then
    running_pid="$(tr -dc '0-9' < "$PID_FILE")"
    if [[ -n "$running_pid" ]] && kill -0 "$running_pid" 2>/dev/null; then
      command_line="$(ps -p "$running_pid" -o command= 2>/dev/null || true)"
      if [[ "$command_line" == *"$SCRIPT_DIR/server.py"* ]]; then
        kill "$running_pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$running_pid" 2>/dev/null || break; sleep 0.1; done
        stopped=1
      fi
    fi
    rm -f "$PID_FILE"
  fi
  if command -v lsof >/dev/null 2>&1; then
    openmagia_listener=0
    if server_is_openmagia; then openmagia_listener=1; fi
    while IFS= read -r listener_pid; do
      [[ -n "$listener_pid" ]] || continue
      command_line="$(ps -p "$listener_pid" -o command= 2>/dev/null || true)"
      # A user may launch a newly downloaded checkout while an older
      # OpenMagia checkout still owns this port. The API probe distinguishes
      # that listener from an unrelated service before allowing replacement.
      if [[ "$command_line" == *"$SCRIPT_DIR/server.py"* ]] || { [[ "$openmagia_listener" -eq 1 ]] && [[ "$command_line" == *"server.py"* ]]; }; then
        kill "$listener_pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do kill -0 "$listener_pid" 2>/dev/null || break; sleep 0.1; done
        stopped=1
      fi
    done < <(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  fi
  [[ "$stopped" -eq 0 ]] || sleep 0.2
  if server_is_ready; then
    echo "Port $PORT is still in use by another application." >&2
    return 1
  fi
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_managed_server
  exit 0
fi

if [[ "${1:-}" == "--restart" ]]; then
  stop_managed_server
  shift
  set -- --background "$@"
fi

if [[ "${1:-}" == "--foreground-log" ]]; then
  mkdir -p "$RUNTIME_DIR"
  exec python3 -u server.py </dev/null >>"$LOG_FILE" 2>&1
fi

if [[ "${1:-}" == "--background" ]]; then
  mkdir -p "$RUNTIME_DIR"
  if [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
    # launchd owns the server rather than the short-lived terminal that asked
    # for it. This is materially different from nohup in sandboxed app hosts,
    # which may reap every descendant when their execution cell closes.
    if launchctl print "gui/$(id -u)/$LAUNCH_LABEL" >/dev/null 2>&1 && server_is_current; then
      echo "OpenMagia is already running under launchd ($LAUNCH_LABEL)"
      exit 0
    fi
    launchctl remove "$LAUNCH_LABEL" >/dev/null 2>&1 || true
    # launchd may reject a shell script located in a protected user folder.
    # Submit the resolved Python executable and server file directly, while
    # explicitly preserving the Homebrew tool path needed by ffmpeg/ffprobe.
    PYTHON_BIN="$(command -v python3)"
    launchctl submit -l "$LAUNCH_LABEL" -- /usr/bin/env \
      "PATH=$PATH" "$PYTHON_BIN" -u "$SCRIPT_DIR/server.py"
    for _ in 1 2 3 4 5; do
      sleep 0.5
      if server_is_current; then
        echo "OpenMagia started under launchd ($LAUNCH_LABEL)"
        exit 0
      fi
    done
    # Registration alone does not mean the process survived. macOS privacy
    # controls can prevent a launchd job from opening a checkout in Documents.
    # Remove the failed keepalive job and use the portable detached launcher.
    launchctl remove "$LAUNCH_LABEL" >/dev/null 2>&1 || true
  fi
  if [[ -f "$PID_FILE" ]]; then
    running_pid="$(tr -dc '0-9' < "$PID_FILE")"
    if [[ -n "$running_pid" ]] && kill -0 "$running_pid" 2>/dev/null; then
      if server_is_current; then
        echo "OpenMagia is already running (PID $running_pid)"
        exit 0
      fi
      echo "Replacing stale OpenMagia server (PID $running_pid)"
      stop_managed_server
    fi
    rm -f "$PID_FILE"
  fi
  # Detach stdin/stdout/stderr from the invoking terminal. This is required
  # when OpenMagia is launched by a short-lived app/agent terminal: otherwise
  # closing that terminal also terminates the server and its active H3 job.
  nohup python3 -u server.py </dev/null >>"$LOG_FILE" 2>&1 &
  server_pid=$!
  echo "$server_pid" > "$PID_FILE"
  ready=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.25
    if server_is_current; then ready=1; break; fi
    kill -0 "$server_pid" 2>/dev/null || break
  done
  if [[ "$ready" -ne 1 ]]; then
    echo "OpenMagia failed to start; see $LOG_FILE" >&2
    rm -f "$PID_FILE"
    exit 1
  fi
  echo "OpenMagia started in background (PID $server_pid, log $LOG_FILE)"
  exit 0
fi

echo "OpenMagia -> http://localhost:$PORT"
exec python3 -u server.py
