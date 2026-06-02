from pathlib import Path


def test_launcher_adds_local_venv_scripts_to_path() -> None:
    launcher = Path("start_meeting_day_recorder.cmd").read_text(encoding="utf-8")

    assert 'set "PATH=%CD%\\.venv\\Scripts;%PATH%"' in launcher
    assert launcher.index('set "PATH=%CD%\\.venv\\Scripts;%PATH%"') < launcher.index(
        '"%PYTHON%" -m app.main'
    )
