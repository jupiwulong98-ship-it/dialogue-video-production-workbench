"""Paths for source checkout and the per-user Windows installer.

Do not write user projects into Program Files or PyInstaller's _internal directory.
"""
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def user_root() -> Path:
    if getattr(sys, "frozen", False) and os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "DialogueWorkbench" if base else Path.home() / "AppData" / "Local" / "DialogueWorkbench"
    return ROOT


def bundled_tool(name: str) -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    suffix = ".exe" if os.name == "nt" else ""
    path = Path(sys.executable).resolve().parent / "tools" / (name + suffix)
    return str(path) if path.is_file() else None


def settings() -> dict:
    storage = user_root()
    local = storage / "settings.local.json"
    overrides = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
    bundled_codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
    default_codex = str(bundled_codex) if os.name != "nt" and bundled_codex.is_file() else shutil.which("codex")
    return {
        "codex": os.environ.get("DIALOGUE_CODEX_PATH") or overrides.get("codex") or default_codex,
        "dreamina_canvas": os.environ.get("DIALOGUE_CANVAS_CLI") or overrides.get("dreamina_canvas") or shutil.which("dreamina-canvas"),
        "ffmpeg": overrides.get("ffmpeg") or bundled_tool("ffmpeg") or shutil.which("ffmpeg"),
        "ffprobe": overrides.get("ffprobe") or bundled_tool("ffprobe") or shutil.which("ffprobe"),
        "model": overrides.get("model") or str((storage if getattr(sys, "frozen", False) else ROOT / ".tools") / "models" / "big-lama.pt"),
        "data": overrides.get("data") or str(storage / "workspaces"),
    }
