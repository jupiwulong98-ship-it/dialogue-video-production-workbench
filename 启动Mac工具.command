#!/bin/zsh
set -eu
cd -- "$(dirname "$0")"
if [[ -d "对话视频工具.app" ]]; then
    exec /usr/bin/open "对话视频工具.app"
fi
exec .venv/bin/python -m dialogue_tool desktop
