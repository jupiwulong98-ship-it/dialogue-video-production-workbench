"""Windows 11 no-dGPU packaging and configuration checks."""
from pathlib import Path

from dialogue_tool import settings as app_settings


def test_cpu_main_package_does_not_require_torch():
    root = Path(__file__).resolve().parents[1]
    content = (root / "pyproject.toml").read_text(encoding="utf-8")
    standard, optional = content.split("[project.optional-dependencies]", 1)
    assert "torch" not in standard
    assert "repair = [" in optional
    assert "torch" in optional


def test_windows_launchers_present_and_cpu_first():
    root = Path(__file__).resolve().parents[1]
    batch = (root / "start_windows.bat").read_text(encoding="utf-8")
    powershell = (root / "setup_windows.ps1").read_text(encoding="utf-8")
    assert "-m dialogue_tool desktop" in batch
    assert ".venv" in batch
    assert ".[desktop]" in powershell
    assert 'py -3.11' in powershell
    assert "CUDA" in powershell
    assert "dreamina-canvas" in powershell


def test_cli_path_overrides_are_cross_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("DIALOGUE_CODEX_PATH", "C:/tools/codex.exe")
    monkeypatch.setenv("DIALOGUE_CANVAS_CLI", "C:/tools/dreamina-canvas.exe")
    monkeypatch.setattr(app_settings, "ROOT", tmp_path)
    cfg = app_settings.settings()
    assert cfg["codex"] == "C:/tools/codex.exe"
    assert cfg["dreamina_canvas"] == "C:/tools/dreamina-canvas.exe"
    assert cfg["data"] == str(tmp_path / "workspaces")
