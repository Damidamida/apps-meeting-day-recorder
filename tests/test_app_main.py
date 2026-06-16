import sys
from pathlib import Path

import app.main as main_module


def test_main_sets_application_icon_from_packaged_resource(
    tmp_path: Path,
    monkeypatch,
) -> None:
    packaged_icon = tmp_path / "app" / "assets" / "bk_scribe.ico"
    packaged_icon.parent.mkdir(parents=True)
    packaged_icon.write_bytes(b"packaged")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    recorded: dict[str, object] = {}

    class RecordingIcon:
        def __init__(self, path: str) -> None:
            recorded["icon_path"] = path

    class FakeApplication:
        def __init__(self, argv: list[str]) -> None:
            recorded["argv"] = argv

        def setWindowIcon(self, icon: RecordingIcon) -> None:
            recorded["app_icon"] = icon

        def exec(self) -> int:
            return 0

    class FakeWindow:
        def show(self) -> None:
            recorded["window_shown"] = True

    monkeypatch.setattr(main_module, "_set_windows_app_id", lambda: None)
    monkeypatch.setattr(main_module, "QApplication", FakeApplication)
    monkeypatch.setattr(main_module, "QIcon", RecordingIcon, raising=False)
    monkeypatch.setattr(main_module, "MainWindow", FakeWindow)

    assert main_module.main() == 0

    assert recorded["icon_path"] == str(packaged_icon)
    assert isinstance(recorded["app_icon"], RecordingIcon)
    assert recorded["window_shown"] is True
