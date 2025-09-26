#!/bin/bash
set -euo pipefail

BASE_DIR="/opt/bmtl-device"
CUR_LINK="$BASE_DIR/current"

resolve_python() {
  local cur_target
  cur_target=$(readlink -f "$CUR_LINK" || true)
  if [ -n "$cur_target" ] && [ -x "$cur_target/venv/bin/python" ]; then
    echo "$cur_target/venv/bin/python"
    return 0
  fi

  case "$(basename "$cur_target" 2>/dev/null)" in
    v1) [ -x "$BASE_DIR/v2/venv/bin/python" ] && echo "$BASE_DIR/v2/venv/bin/python" && return 0 ;;
    v2) [ -x "$BASE_DIR/v1/venv/bin/python" ] && echo "$BASE_DIR/v1/venv/bin/python" && return 0 ;;
  esac

  if command -v python3 >/dev/null 2>&1; then
    echo "$(command -v python3)"
    return 0
  fi

  echo "Unable to locate Python interpreter for bmtl-camera" >&2
  return 1
}

PYTHON_BIN="$(resolve_python)" || exit 203

CUR_TARGET=$(readlink -f "$CUR_LINK" || true)
MAIN_PATH="$CUR_LINK/camera_daemon.py"
if [ -n "$CUR_TARGET" ] && [ -f "$CUR_TARGET/camera_daemon.py" ]; then
  MAIN_PATH="$CUR_TARGET/camera_daemon.py"
  export PYTHONPATH="$CUR_TARGET:${PYTHONPATH:-}"
  cd "$CUR_TARGET" || true
else
  export PYTHONPATH="$CUR_LINK:${PYTHONPATH:-}"
  cd "$CUR_LINK" 2>/dev/null || true
fi

exec "$PYTHON_BIN" "$MAIN_PATH"
