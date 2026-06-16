from app.services import subprocess_utils


def test_hidden_process_kwargs_hides_console_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(subprocess_utils.sys, "platform", "win32")
    monkeypatch.setattr(subprocess_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    assert subprocess_utils.hidden_process_kwargs() == {"creationflags": 0x08000000}


def test_hidden_process_kwargs_empty_outside_windows(monkeypatch) -> None:
    monkeypatch.setattr(subprocess_utils.sys, "platform", "linux")

    assert subprocess_utils.hidden_process_kwargs() == {}
