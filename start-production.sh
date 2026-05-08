#!/bin/bash
set -e

# Start the Telegram bot with auto-restart in background
(while true; do
    echo "[bot] Starting Telegram bot..."
    python telegram-bot/bot.py || true
    echo "[bot] Bot exited, restarting in 5 seconds..."
    sleep 5
done) &

# Start the API server in the foreground (keeps the VM alive via health check)
echo "[api] Starting API server..."
exec node --enable-source-maps artifacts/api-server/dist/index.mjs
