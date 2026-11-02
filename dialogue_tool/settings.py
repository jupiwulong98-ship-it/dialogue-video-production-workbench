import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def settings() -> dict:
    local = ROOT / 'settings.local.json'
    overrides = json.loads(local.read_text(encoding='utf-8')) if local.exists() else {}
    bundled_codex = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex')
    default_codex = str(bundled_codex) if bundled_codex.is_file() else shutil.which('codex')
    return {
        'codex': os.environ.get('DIALOGUE_CODEX_PATH') or overrides.get('codex') or default_codex,
        'ffmpeg': overrides.get('ffmpeg') or shutil.which('ffmpeg'),
        'ffprobe': overrides.get('ffprobe') or shutil.which('ffprobe'),
        'model': overrides.get('model') or str(ROOT / '.tools' / 'models' / 'big-lama.pt'),
        'data': overrides.get('data') or str(ROOT / 'workspaces'),
    }
