#!/usr/bin/env bash
set -euo pipefail

whisper_version="v1.9.1"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/telegram-spendee-bot"
source_dir="$data_root/whisper.cpp"
binary="$source_dir/build/bin/whisper-cli"
model="$source_dir/models/ggml-base.bin"
yazio_root="${XDG_DATA_HOME:-$HOME/.local/share}/telegram-yazio-bot/whisper.cpp"

for command_name in git cmake; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 127
  }
done
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Missing required command: ffmpeg. Install the ffmpeg system package first." >&2
  exit 127
fi

mkdir -p "$data_root"
if [[ ! -e "$source_dir" ]] &&
  [[ -x "$yazio_root/build/bin/whisper-cli" ]] &&
  [[ -s "$yazio_root/models/ggml-base.bin" ]]; then
  ln -s "$yazio_root" "$source_dir"
  echo "Reusing whisper.cpp installed for telegram-yazio-bot"
fi

if [[ -x "$binary" ]] && [[ -s "$model" ]]; then
  installed_version="$(git -C "$source_dir" describe --tags --exact-match 2>/dev/null || true)"
  if [[ "$installed_version" == "$whisper_version" ]]; then
    echo "whisper.cpp $whisper_version and multilingual base model are ready"
    exit 0
  fi
fi

if [[ ! -d "$source_dir/.git" ]]; then
  git clone --branch "$whisper_version" --depth 1 \
    https://github.com/ggml-org/whisper.cpp.git "$source_dir"
else
  installed_version="$(git -C "$source_dir" describe --tags --exact-match 2>/dev/null || true)"
  if [[ "$installed_version" != "$whisper_version" ]]; then
    echo "Existing whisper.cpp at $source_dir is $installed_version; expected $whisper_version" >&2
    exit 78
  fi
fi

cmake -S "$source_dir" -B "$source_dir/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$source_dir/build" --config Release --target whisper-cli --parallel 1

if [[ ! -s "$model" ]]; then
  "$source_dir/models/download-ggml-model.sh" base
fi

test -x "$binary"
test -s "$model"
echo "Installed whisper.cpp $whisper_version and multilingual base model"
