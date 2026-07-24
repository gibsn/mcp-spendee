#!/usr/bin/env bash
set -euo pipefail

repo_dir="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
remote="${UPDATE_REMOTE:-origin}"
branch="${UPDATE_BRANCH:-main}"
service="${SERVICE_NAME:-telegram-spendee-bot.service}"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/aitools/telegram-spendee-bot"
revision_file="$state_dir/deployed-revision"
lock_file="$state_dir/update.lock"
telegram_helper="${CODEX_HOME:-$HOME/.codex}/bin/send_codex_telegram"

notify() {
  if [[ -x "$telegram_helper" ]]; then
    "$telegram_helper" "#telegram_spendee_bot $*" >/dev/null 2>&1 || \
      echo "Could not send Telegram deploy notification" >&2
  fi
}

mkdir -p "$state_dir"
chmod 700 "$state_dir"
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "Another update is already running"
  exit 0
fi

failure_notification() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    notify "Автовыкладка из ${remote}/${branch} завершилась ошибкой на $(hostname -f 2>/dev/null || hostname). Смотрите journalctl --user -u telegram-spendee-bot-update.service."
  fi
}
trap failure_notification EXIT

cd "$repo_dir"

current_branch="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ "$current_branch" != "$branch" ]]; then
  echo "Expected branch $branch, found ${current_branch:-detached HEAD}" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Working tree is dirty; refusing to auto-update $repo_dir" >&2
  exit 1
fi

git fetch --prune "$remote" "$branch"
old_head="$(git rev-parse HEAD)"
remote_head="$(git rev-parse "$remote/$branch")"

if [[ "$old_head" != "$remote_head" ]]; then
  if ! git merge-base --is-ancestor "$old_head" "$remote_head"; then
    echo "Remote update is not a fast-forward from $old_head to $remote_head" >&2
    exit 1
  fi
  git merge --ff-only "$remote/$branch"
fi

deployed_head=""
if [[ -r "$revision_file" ]]; then
  deployed_head="$(tr -d '\r\n' < "$revision_file")"
fi
if [[ "$deployed_head" == "$remote_head" ]] && systemctl --user is-active --quiet "$service"; then
  echo "No changes in $remote/$branch"
  exit 0
fi

make test
make lint
"$repo_dir/scripts/install-systemd.sh" --start

temporary_revision="$(mktemp "$state_dir/.deployed-revision.XXXXXX")"
printf '%s\n' "$remote_head" > "$temporary_revision"
chmod 600 "$temporary_revision"
mv "$temporary_revision" "$revision_file"

old_short="$(git rev-parse --short "$old_head")"
new_short="$(git rev-parse --short "$remote_head")"
notify "Выкладка ${service} на $(hostname -f 2>/dev/null || hostname): ${old_short} → ${new_short} (${remote}/${branch})."
trap - EXIT
echo "Updated $service from $old_head to $remote_head"
