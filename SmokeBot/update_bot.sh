#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="/home/netherslayer87"
RAW_BASE="https://raw.githubusercontent.com/SmokeSlate/discordBots/main/SmokeBot"
PID_FILE="$BOT_DIR/pid.txt"
LOG_FILE="$BOT_DIR/discord.log"
MAIN_FILE="$BOT_DIR/main.py"
ENV_FILE="$BOT_DIR/.smokebot_env"
SCRIPT_FILES=(
  "main.py"
  "storage.py"
  "auto_update.py"
)

cd "$BOT_DIR"

echo "Stopping bot..."
DID_KILL=0
if [[ -f "$PID_FILE" ]]; then
  BOT_PID="$(cat "$PID_FILE")"
  if kill "$BOT_PID" 2>/dev/null; then
    echo "Bot stopped."
    DID_KILL=1
  else
    echo "PID file existed but process was already gone."
  fi
  rm -f "$PID_FILE"
fi

if [[ "$DID_KILL" != "1" ]]; then
  echo "Trying to stop any existing main.py process..."
  pkill -f "python3 .*main.py" 2>/dev/null || true
fi

echo "Updating bot files..."
for file in "${SCRIPT_FILES[@]}"; do
  curl -fsSL -o "$BOT_DIR/$file" "$RAW_BASE/$file"
done

echo "Starting bot..."
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi
nohup python3 "$MAIN_FILE" > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Started with PID $(cat "$PID_FILE")"
