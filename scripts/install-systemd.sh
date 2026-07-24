#!/usr/bin/env bash
set -euo pipefail

start_service="0"
if [[ "${1:-}" == "--start" ]]; then
  start_service="1"
elif [[ $# -gt 0 ]]; then
  echo "Usage: install-systemd.sh [--start]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
codex_home="${CODEX_HOME:-$HOME/.codex}"
env_dir="$codex_home/secrets"
env_file="$env_dir/telegram-spendee-bot.env"
binary="$repo_root/.venv/bin/telegram-spendee-bot"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/aitools/telegram-spendee-bot"
state_file="$state_dir/state.json"
systemd_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
service_file="$systemd_dir/telegram-spendee-bot.service"
update_service_file="$systemd_dir/telegram-spendee-bot-update.service"
update_timer_file="$systemd_dir/telegram-spendee-bot-update.timer"

for command_name in uv codex systemctl ffmpeg flock; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 127
  }
done

"$repo_root/scripts/install-whisper.sh"

mkdir -p "$env_dir" "$state_dir" "$systemd_dir"
chmod 700 "$env_dir" "$state_dir"

if [[ ! -e "$env_file" ]]; then
  install -m 0600 "$repo_root/config/telegram.env.example" "$env_file"
  echo "Created local configuration: $env_file"
else
  chmod 600 "$env_file"
  echo "Keeping existing local configuration: $env_file"
fi

uv sync --all-groups
test -x "$binary"

sed \
  -e "s|@HOME@|$HOME|g" \
  -e "s|@CODEX_HOME@|$codex_home|g" \
  -e "s|@BOT_REPO@|$repo_root|g" \
  -e "s|@STATE_FILE@|$state_file|g" \
  -e "s|@ENV_FILE@|$env_file|g" \
  -e "s|@BINARY@|$binary|g" \
  "$repo_root/deploy/telegram-spendee-bot.service.in" > "$service_file"

sed \
  -e "s|@HOME@|$HOME|g" \
  -e "s|@CODEX_HOME@|$codex_home|g" \
  -e "s|@BOT_REPO@|$repo_root|g" \
  "$repo_root/deploy/telegram-spendee-bot-update.service.in" > "$update_service_file"
install -m 0644 \
  "$repo_root/deploy/telegram-spendee-bot-update.timer" \
  "$update_timer_file"

systemctl --user daemon-reload
systemctl --user enable telegram-spendee-bot.service
current_branch="$(git -C "$repo_root" symbolic-ref --quiet --short HEAD || true)"
if [[ "$current_branch" == "main" ]]; then
  systemctl --user enable --now telegram-spendee-bot-update.timer
else
  echo "Update timer is not enabled on ${current_branch:-detached HEAD}; run this installer again from main"
fi

if [[ "$start_service" == "1" ]]; then
  if ! grep -Eq '^TELEGRAM_BOT_TOKEN=.+$' "$env_file"; then
    echo "TELEGRAM_BOT_TOKEN is empty in $env_file; service was installed but not started" >&2
    exit 78
  fi
  systemctl --user restart telegram-spendee-bot.service
  echo "Started telegram-spendee-bot.service"
else
  echo "Installed and enabled telegram-spendee-bot.service (not started)"
fi
