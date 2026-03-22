#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="/home/netherslayer87"
RAW_BASE="https://raw.githubusercontent.com/SmokeSlate/discordBots/main/SmokeBot"
PID_FILE="$BOT_DIR/pid.txt"
LOG_FILE="$BOT_DIR/discord.log"
MAIN_FILE="$BOT_DIR/main.py"
SCRIPT_FILES=(
  "main.py"
  "storage.py"
  "auto_update.py"
)

cd "$BOT_DIR"

echo "Stopping bot..."
if [[ -f "$PID_FILE" ]]; then
  BOT_PID="$(cat "$PID_FILE")"
  if kill "$BOT_PID" 2>/dev/null; then
    echo "Bot stopped."
  else
    echo "PID file existed but process was already gone."
  fi
  rm -f "$PID_FILE"
else
  echo "No PID file found, trying to stop any existing main.py process..."
  pkill -f "python3 .*main.py" 2>/dev/null || true
fi

echo "Updating bot files..."
for file in "${SCRIPT_FILES[@]}"; do
  curl -fsSL -o "$BOT_DIR/$file" "$RAW_BASE/$file"
done

echo "Starting bot..."
nohup python3 "$MAIN_FILE" > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Started with PID $(cat "$PID_FILE")"
