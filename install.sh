#!/usr/bin/env bash
#
# install.sh — one-command, step-by-step installer for Football-bot.
#
#   curl -fsSL https://raw.githubusercontent.com/mariamtchelidze66/Football-bot/main/install.sh | bash
#   # or:
#   wget -qO- https://raw.githubusercontent.com/mariamtchelidze66/Football-bot/main/install.sh | bash
#
# It asks before every step (y/n), auto-detects the OS/package manager, works
# whether piped into bash or run directly, and needs NO GitHub token (the repo
# is expected to be public). No secrets are stored in this repo.
#
# Football-bot is a football-prediction Telegram bot. The bot itself is Python
# (telegram-bot/bot.py, run via telegram-bot/watchdog.py). There is also an
# optional Node/pnpm workspace that only serves a small keep-alive/status API
# page (artifacts/api-server) used by start-production.sh; the bot does NOT
# depend on it (bot.py runs its own health server on port 8765).
#
set -uo pipefail

# ---------- project settings (change these per repo) ----------
PROJECT="Football-bot"
REPO_URL="https://github.com/mariamtchelidze66/Football-bot.git"
RAW_URL="https://raw.githubusercontent.com/mariamtchelidze66/Football-bot/main/install.sh"
DEST="${FOOTBALL_BOT_DIR:-$HOME/Football-bot}"
# system packages this project needs (Python is required; Node is optional):
PKGS_APT="git python3 python3-venv python3-pip"
PKGS_PACMAN="git python python-pip"
PKGS_DNF="git python3 python3-pip"
# extra packages if the user also wants the optional Node API server:
NODE_APT="nodejs npm"
NODE_PACMAN="nodejs npm"
NODE_DNF="nodejs npm"

# ---------- colors / logging ----------
if [[ -t 1 ]]; then
  R="\033[31m"; G="\033[32m"; Y="\033[33m"; C="\033[36m"; B="\033[1m"; N="\033[0m"
else R=""; G=""; Y=""; C=""; B=""; N=""; fi
info(){ echo -e "${C}[*]${N} $*"; }
ok(){   echo -e "${G}[+]${N} $*"; }
warn(){ echo -e "${Y}[!]${N} $*"; }
err(){  echo -e "${R}[x]${N} $*" >&2; }
step(){ echo; echo -e "${B}==== $* ====${N}"; }

# ---------- read from the real terminal even when piped (curl | bash) ----------
# Open the actual controlling terminal on fd 3 so prompts work even when this
# script itself arrives on stdin (curl ... | bash). If there is no terminal
# (CI, plain pipe), we must NOT silently assume "yes" — set ASSUME_YES=1 to
# opt into a fully non-interactive run instead.
HAVE_TTY=0
if { exec 3</dev/tty; } 2>/dev/null; then HAVE_TTY=1; fi
ASSUME_YES="${ASSUME_YES:-0}"
need_tty(){
  [[ $HAVE_TTY -eq 1 || "$ASSUME_YES" == "1" ]] && return 0
  err "No interactive terminal detected."
  err "This installer asks questions, so run it one of these ways:"
  err "   bash <(curl -fsSL $RAW_URL)        # keeps the terminal for prompts"
  err "   curl -fsSL $RAW_URL | ASSUME_YES=1 bash   # non-interactive, accept all"
  exit 1
}
ask(){  # ask "question" [default y|n] -> returns 0 for yes
  local q="$1" def="${2:-y}" ans hint="[Y/n]"
  [[ "$def" == "n" ]] && hint="[y/N]"
  if [[ "$ASSUME_YES" == "1" ]]; then ans="$def"
  else read -rp "$(echo -e "${Y}[?]${N} $q $hint ")" -u 3 ans || ans=""; fi
  ans="${ans:-$def}"
  [[ "$ans" =~ ^[Yy]$ ]]
}
prompt(){  # prompt "question" -> echoes the typed line
  local q="$1" ans
  if [[ "$ASSUME_YES" == "1" ]]; then echo ""; return; fi
  read -rp "$(echo -e "${Y}[?]${N} $q ")" -u 3 ans || ans=""
  echo "$ans"
}

# ---------- detect environment ----------
step "0) Detecting environment"
OS="$(uname -s)"
IS_WSL=0
grep -qiE "microsoft|wsl" /proc/version 2>/dev/null && IS_WSL=1
if   command -v apt    >/dev/null 2>&1; then PM=apt;    PKGS="$PKGS_APT";    NODE_PKGS="$NODE_APT"
elif command -v pacman >/dev/null 2>&1; then PM=pacman; PKGS="$PKGS_PACMAN"; NODE_PKGS="$NODE_PACMAN"
elif command -v dnf    >/dev/null 2>&1; then PM=dnf;    PKGS="$PKGS_DNF";    NODE_PKGS="$NODE_DNF"
else PM=""; PKGS="$PKGS_APT"; NODE_PKGS="$NODE_APT"; fi
info "OS: $OS   WSL: $([[ $IS_WSL -eq 1 ]] && echo yes || echo no)   package manager: ${PM:-unknown}"
need_tty   # bail out early if we can't ask questions and ASSUME_YES isn't set
[[ "$OS" != "Linux" ]] && warn "This tool targets Linux; other systems are untested."
if [[ $IS_WSL -eq 1 ]]; then
  info "You're on WSL — that's fine; this is a network Telegram bot, no special hardware needed."
fi

# sudo helper (root runs commands directly)
SUDO=""
if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else warn "Not root and no sudo; package install may fail."; fi
fi

echo
info "About to install '${PROJECT}':"
info "  1. install system packages:  $PKGS   (Python bot; Node is optional)"
info "  2. clone the repo into:       $DEST"
info "  3. install Python deps (uv or pip, from pyproject.toml)"
info "  4. (optional) install the Node/pnpm keep-alive API server"
info "  5. write a blank .env for your secrets (Telegram token, API keys)"
info "  6. optionally start the bot"
ask "Continue?" y || { err "Aborted by user."; exit 1; }

# ---------- 1) system packages ----------
step "1) System packages"
if [[ -z "$PM" ]]; then
  err "No known package manager (apt/pacman/dnf). Install manually: $PKGS"
elif ask "Install/verify packages ($PKGS)?" y; then
  case "$PM" in
    apt)    $SUDO apt-get update -y && $SUDO apt-get install -y $PKGS ;;
    pacman) $SUDO pacman -Sy --noconfirm $PKGS ;;
    dnf)    $SUDO dnf install -y $PKGS ;;
  esac && ok "Packages ready." || err "Package install had errors (continuing)."
else
  warn "Skipped package install."
fi

# ---------- 2) clone / update the repo ----------
step "2) Get the code"
if [[ -d "$DEST/.git" ]]; then
  info "$DEST already exists."
  if ask "Update it (git pull)?" y; then git -C "$DEST" pull --ff-only || warn "pull failed."; fi
else
  if ask "Clone $REPO_URL into $DEST?" y; then
    git clone "$REPO_URL" "$DEST" && ok "Cloned into $DEST" || { err "Clone failed."; exit 1; }
  else
    warn "Skipped clone — nothing to run."; exit 0
  fi
fi
chmod +x "$DEST/start-production.sh" 2>/dev/null || true

# ---------- 3) Python dependencies ----------
step "3) Python dependencies"
# The bot needs: python-telegram-bot[job-queue], anthropic (see pyproject.toml)
# and requests (imported by telegram-bot/bot.py). Prefer uv if available (there
# is a uv.lock); otherwise fall back to a venv + pip install from pyproject.
PYBIN="python3"; command -v python3 >/dev/null 2>&1 || PYBIN="python"
if ask "Install Python dependencies now?" y; then
  if command -v uv >/dev/null 2>&1; then
    info "Using uv (uv.lock present)."
    ( cd "$DEST" && uv sync ) && ok "Python deps installed via uv." \
      || err "uv sync had errors (continuing)."
  else
    info "uv not found — using a virtualenv + pip from pyproject.toml."
    if ask "Create venv at $DEST/.venv and pip install?" y; then
      "$PYBIN" -m venv "$DEST/.venv" \
        && "$DEST/.venv/bin/pip" install --upgrade pip \
        && "$DEST/.venv/bin/pip" install \
             "python-telegram-bot[job-queue]>=22.7" "anthropic>=0.100.0" requests \
        && ok "Python deps installed into $DEST/.venv." \
        || err "pip install had errors (continuing)."
      info "Tip: install uv (https://astral.sh/uv) to use the pinned uv.lock instead."
    else
      warn "Skipped venv/pip. Install manually from pyproject.toml before running."
    fi
  fi
else
  warn "Skipped Python dependency install."
fi

# ---------- 4) (optional) Node / pnpm API server ----------
step "4) Optional Node keep-alive API server"
info "The Telegram bot is pure Python and runs on its own (health server :8765)."
info "start-production.sh ALSO launches a small Node/Express status page"
info "(artifacts/api-server) as a Replit keep-alive. This is OPTIONAL."
if ask "Set up the Node/pnpm API server too?" n; then
  if [[ -n "$PM" ]] && ask "Install Node packages ($NODE_PKGS)?" y; then
    case "$PM" in
      apt)    $SUDO apt-get install -y $NODE_PKGS ;;
      pacman) $SUDO pacman -Sy --noconfirm $NODE_PKGS ;;
      dnf)    $SUDO dnf install -y $NODE_PKGS ;;
    esac && ok "Node ready." || err "Node install had errors (continuing)."
  fi
  # This is a pnpm workspace (pnpm-workspace.yaml, .npmrc). Get pnpm via corepack
  # if available, otherwise via npm.
  if ! command -v pnpm >/dev/null 2>&1; then
    if command -v corepack >/dev/null 2>&1; then
      info "Enabling pnpm via corepack…"
      $SUDO corepack enable 2>/dev/null || corepack enable 2>/dev/null || true
      corepack prepare pnpm@latest --activate 2>/dev/null || true
    fi
    if ! command -v pnpm >/dev/null 2>&1 && ask "pnpm still missing — 'npm i -g pnpm'?" y; then
      $SUDO npm install -g pnpm || npm install -g pnpm || warn "pnpm install failed."
    fi
  fi
  if command -v pnpm >/dev/null 2>&1; then
    ( cd "$DEST" && pnpm install && pnpm run build ) \
      && ok "Node workspace installed & built." \
      || err "pnpm install/build had errors (continuing)."
  else
    warn "pnpm unavailable — skipped Node install. Enable corepack or 'npm i -g pnpm'."
  fi
else
  info "Skipping Node — the Python bot works fine without it."
fi

# ---------- 5) config / secrets ----------
step "5) Configuration & connection test"
# The bot reads these from the environment (see telegram-bot/bot.py):
#   TELEGRAM_BOT_TOKEN  (required)  — from @BotFather
#   ANTHROPIC_API_KEY   (required)  — from console.anthropic.com
#   APISPORTS_KEY       (optional)  — football data from api-sports.io
# This step ASKS for each secret and TESTS the connection before saving.
ENV_FILE="$DEST/.env"

# read a secret from the real terminal (hidden input, via fd 3)
ask_secret(){  # ask_secret "prompt" -> echoes typed value
  local q="$1" ans=""
  if [[ "$ASSUME_YES" == "1" ]]; then echo ""; return; fi
  read -rsp "$(echo -e "${Y}[?]${N} $q ")" -u 3 ans || ans=""
  echo >&2   # newline after the hidden input
  echo "$ans"
}
test_telegram(){  # $1=token -> 0 if Telegram accepts it
  local out
  out=$(curl -fsS --max-time 15 "https://api.telegram.org/bot$1/getMe" 2>/dev/null) || return 1
  echo "$out" | grep -q '"ok":true'
}
test_anthropic(){  # $1=key -> 0 if the key authenticates
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
    https://api.anthropic.com/v1/models \
    -H "x-api-key: $1" -H "anthropic-version: 2023-06-01" 2>/dev/null)
  [[ "$code" == "200" ]]
}

TELEGRAM_BOT_TOKEN=""; ANTHROPIC_API_KEY=""; APISPORTS_KEY=""

if [[ -f "$ENV_FILE" ]] && ! ask ".env already exists at $ENV_FILE — overwrite it?" n; then
  info "Keeping existing .env; loading it to re-test the connections."
  set -a; . "$ENV_FILE"; set +a
else
  # --- Telegram bot token (required) ---
  while :; do
    TELEGRAM_BOT_TOKEN="$(ask_secret 'Telegram bot token (from @BotFather):')"
    if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
      warn "Empty token — the bot cannot run without it."
      ask "Skip for now and fill .env manually?" n && break || continue
    fi
    info "Testing the Telegram token…"
    if test_telegram "$TELEGRAM_BOT_TOKEN"; then ok "Telegram token works — connected ✅"; break
    else err "Telegram rejected that token."; ask "Try a different token?" y || break; fi
  done
  # --- Anthropic API key (required) ---
  while :; do
    ANTHROPIC_API_KEY="$(ask_secret 'Anthropic API key (from console.anthropic.com):')"
    if [[ -z "$ANTHROPIC_API_KEY" ]]; then
      warn "Empty key — the bot cannot run without it."
      ask "Skip for now and fill .env manually?" n && break || continue
    fi
    info "Testing the Anthropic key…"
    if test_anthropic "$ANTHROPIC_API_KEY"; then ok "Anthropic key works — connected ✅"; break
    else err "Anthropic rejected that key (or no network)."; ask "Try a different key?" y || break; fi
  done
  # --- optional football data key ---
  if ask "Add an optional API-Sports football key now?" n; then
    APISPORTS_KEY="$(ask_secret 'API-Sports key (leave blank to skip):')"
  fi
  # write .env with the values
  umask 077
  cat > "$ENV_FILE" <<EOF
# Football-bot secrets — do NOT commit this file.
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
APISPORTS_KEY=${APISPORTS_KEY}
EOF
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  ok "Saved $ENV_FILE (chmod 600)."
fi

# --- final connectivity summary ---
step "5b) Connection summary"
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && test_telegram "$TELEGRAM_BOT_TOKEN"; then
  ok "Telegram: connected ✅"
else warn "Telegram: NOT verified — check TELEGRAM_BOT_TOKEN in $ENV_FILE"; fi
if [[ -n "${ANTHROPIC_API_KEY:-}" ]] && test_anthropic "$ANTHROPIC_API_KEY"; then
  ok "Anthropic: connected ✅"
else warn "Anthropic: NOT verified — check ANTHROPIC_API_KEY in $ENV_FILE"; fi

# ---------- 6) run / test ----------
step "6) Run the bot"
if command -v uv >/dev/null 2>&1; then
  RUN_HINT="cd $DEST && set -a; . .env; set +a; uv run python telegram-bot/watchdog.py"
elif [[ -x "$DEST/.venv/bin/python" ]]; then
  RUN_HINT="cd $DEST && set -a; . .env; set +a; .venv/bin/python telegram-bot/watchdog.py"
else
  RUN_HINT="cd $DEST && set -a; . .env; set +a; python3 telegram-bot/watchdog.py"
fi
ok "Installed. To run the bot later (loads .env then starts watchdog+bot):"
ok "   $RUN_HINT"
info "For the full Replit-style run (bot + Node API server): $DEST/start-production.sh"
if ask "Start the bot now (needs TELEGRAM_BOT_TOKEN + ANTHROPIC_API_KEY set)?" n; then
  if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${ANTHROPIC_API_KEY:-}" ]]; then
    err "TELEGRAM_BOT_TOKEN and/or ANTHROPIC_API_KEY are empty — edit $ENV_FILE first."
  else
    if command -v uv >/dev/null 2>&1; then
      ( cd "$DEST" && uv run python telegram-bot/watchdog.py ) <&3
    elif [[ -x "$DEST/.venv/bin/python" ]]; then
      ( cd "$DEST" && .venv/bin/python telegram-bot/watchdog.py ) <&3
    else
      ( cd "$DEST" && "$PYBIN" telegram-bot/watchdog.py ) <&3
    fi
  fi
else
  info "You can start it anytime with the command shown above."
fi

echo
ok "Done."
