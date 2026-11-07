#!/usr/bin/env bash
set -Eeuo pipefail

BOT_DIR="${SMOKEBOT_BOT_DIR:-/home/netherslayer87}"
RAW_BASE="${SMOKEBOT_UPDATE_BASE:-https://raw.githubusercontent.com/SmokeSlate/discordBots/main/SmokeBot}"
PID_FILE="$BOT_DIR/pid.txt"
LOG_FILE="$BOT_DIR/discord.log"
MAIN_FILE="$BOT_DIR/main.py"
ENV_FILE="$BOT_DIR/.smokebot_env"
SCRIPT_FILES=(
  "main.py"
  "storage.py"
  "auto_update.py"
  "chat_utils.py"
)

cd "$BOT_DIR"
STAGING="$(mktemp -d "$BOT_DIR/.smokebot-update.XXXXXX")"
mkdir "$STAGING/download" "$STAGING/backup"
REPLACEMENT_STARTED=0
WAS_RUNNING=0

start_bot() {
  nohup python3 "$MAIN_FILE" > "$LOG_FILE" 2>&1 &
  NEW_PID=$!
  echo "$NEW_PID" > "$PID_FILE"
  sleep 1
  kill -0 "$NEW_PID" 2>/dev/null
}

rollback() {
  local result=$?
  trap - ERR INT TERM
  set +e
  if [[ "$REPLACEMENT_STARTED" == 1 ]]; then
    echo "Update failed; restoring previous files." >&2
    for file in "${SCRIPT_FILES[@]}"; do
      if [[ -f "$STAGING/backup/$file" ]]; then
        mv -f "$STAGING/backup/$file" "$BOT_DIR/$file"
      else
        rm -f "$BOT_DIR/$file"
      fi
    done
    rm -f "$PID_FILE"
    if [[ "$WAS_RUNNING" == 1 ]]; then
      start_bot || echo "Previous bot did not restart; manual recovery required." >&2
    fi
  fi
  exit "${result:-1}"
}
trap 'rm -rf "$STAGING"' EXIT
trap rollback ERR
trap 'false' INT TERM

# Download and validate every module before touching live files or process state.
echo "Staging bot files..."
for file in "${SCRIPT_FILES[@]}"; do
  curl -fsSL --connect-timeout 10 --max-time 60 -o "$STAGING/download/$file" "$RAW_BASE/$file"
  [[ -s "$STAGING/download/$file" ]]
done
python3 -m py_compile "$STAGING/download/"*.py
for file in "${SCRIPT_FILES[@]}"; do
  if [[ -f "$BOT_DIR/$file" ]]; then
    cp -p "$BOT_DIR/$file" "$STAGING/backup/$file"
  fi
done
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

if [[ -f "$PID_FILE" ]]; then
  BOT_PID="$(cat "$PID_FILE")"
  if [[ "$BOT_PID" =~ ^[0-9]+$ ]] && [[ "$BOT_PID" -gt 1 ]] && kill -0 "$BOT_PID" 2>/dev/null; then
    echo "Stopping tracked bot process..."
    kill "$BOT_PID"
    WAS_RUNNING=1
    for attempt in {1..40}; do
      if ! kill -0 "$BOT_PID" 2>/dev/null; then break; fi
      sleep 0.25
    done
    if kill -0 "$BOT_PID" 2>/dev/null; then
      echo "Tracked process did not stop; refusing replacement." >&2
      REPLACEMENT_STARTED=0
      exit 1
    fi
  fi
  REPLACEMENT_STARTED=1
  rm -f "$PID_FILE"
fi

REPLACEMENT_STARTED=1
for file in "${SCRIPT_FILES[@]}"; do
  mv -f "$STAGING/download/$file" "$BOT_DIR/$file"
done
echo "Starting bot..."
start_bot
REPLACEMENT_STARTED=0
echo "Started with PID $NEW_PID"
