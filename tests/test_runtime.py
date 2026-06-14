import sys
from pathlib import Path

from app.runtime import app_root, bundled_tool_path, resource_path


def test_resource_path_uses_pyinstaller_meipass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resource_path("app/assets/bk_scribe.ico") == tmp_path / "app" / "assets" / "bk_scribe.ico"


def test_app_root_uses_executable_parent_in_packaged_mode(monkeypatch, tmp_path: Path) -> None:
    exe = tmp_path / "BK Scribe.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert app_root() == tmp_path


def test_bundled_tool_path_points_to_resources_folder(monkeypatch, tmp_path: Path) -> None:
    resources_root = tmp_path / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(resources_root), raising=False)

    assert bundled_tool_path("ffmpeg.exe") == resources_root / "resources" / "ffmpeg" / "ffmpeg.exe"
