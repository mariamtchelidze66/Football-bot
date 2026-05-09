"""
Watchdog for the Telegram bot.

- Starts bot.py as a subprocess.
- Restarts it automatically if it crashes (5 s cool-down).
- Pings the health endpoint every 60 seconds and restarts if it fails twice in a row
  (i.e. bot is restarted within 2 minutes of becoming unresponsive).
"""
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

logging.basicConfig(
    format="%(asctime)s - watchdog - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("watchdog")

HEALTH_URL = "http://localhost:8765/health"
CHECK_INTERVAL = 60         # seconds between health pings (1 min)
STARTUP_GRACE = 20          # seconds to wait after start before first health check
CRASH_COOLDOWN = 5          # seconds to wait before restarting after a crash
MAX_CONSECUTIVE_FAILS = 2   # 2 failed pings × 60 s = restart within 2 minutes

BOT_CMD = [sys.executable, "telegram-bot/bot.py"]


def ping_health() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_bot() -> subprocess.Popen:
    logger.info("Starting bot process: %s", " ".join(BOT_CMD))
    proc = subprocess.Popen(BOT_CMD)
    logger.info("Bot PID: %d", proc.pid)
    return proc


def kill_bot(proc: subprocess.Popen) -> None:
    logger.info("Terminating bot PID %d …", proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("Bot did not exit cleanly; sending SIGKILL")
        proc.kill()
        proc.wait()
    logger.info("Bot process stopped")


def main() -> None:
    consecutive_fails = 0

    while True:
        proc = start_bot()
        time.sleep(STARTUP_GRACE)

        while True:
            # --- Check if the process is still alive ---
            exit_code = proc.poll()
            if exit_code is not None:
                logger.warning(
                    "Bot process exited with code %d — restarting in %ds",
                    exit_code, CRASH_COOLDOWN,
                )
                time.sleep(CRASH_COOLDOWN)
                break   # outer loop will start a fresh process

            # --- Health check ---
            if ping_health():
                logger.info("Health check OK (pid=%d)", proc.pid)
                consecutive_fails = 0
            else:
                consecutive_fails += 1
                logger.warning(
                    "Health check FAILED (%d/%d)", consecutive_fails, MAX_CONSECUTIVE_FAILS
                )
                if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                    logger.error(
                        "Health check failed %d times in a row — restarting bot",
                        consecutive_fails,
                    )
                    kill_bot(proc)
                    consecutive_fails = 0
                    time.sleep(CRASH_COOLDOWN)
                    break   # outer loop restarts

            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
