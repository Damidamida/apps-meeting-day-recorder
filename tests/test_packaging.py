from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_pyinstaller_spec_builds_bk_scribe_executable_with_icon_and_resources() -> None:
    spec = ROOT / "packaging" / "pyinstaller" / "bk_scribe.spec"
    text = spec.read_text(encoding="utf-8")

    assert "name='BK Scribe'" in text
    assert "icon=" in text
    assert "bk_scribe.ico" in text
    assert "app/assets" in text
    assert "resources/ffmpeg" in text


def test_inno_script_is_per_user_bk_scribe_installer() -> None:
    script = ROOT / "packaging" / "inno" / "bk_scribe.iss"
    text = script.read_text(encoding="utf-8")

    assert "AppName=BK Scribe" in text
    assert "DefaultDirName={localappdata}\\BK Scribe" in text
    assert "PrivilegesRequired=lowest" in text
    assert "DisableDirPage=no" in text
    assert "Name: \"desktopicon\"; Description: \"Создать ярлык на рабочем столе\"" in text
    assert "Name: \"{autoprograms}\\BK Scribe\"" in text
    assert "UninstallDisplayIcon={app}\\BK Scribe.exe" in text
    assert "Description: \"Запустить BK Scribe\"" in text
    assert "config.yaml" not in text
    assert ".env" not in text


def test_windows_package_script_uses_pyinstaller_and_inno_setup() -> None:
    script = ROOT / "scripts" / "build_windows_package.ps1"
    text = script.read_text(encoding="utf-8")

    assert "pyinstaller" in text
    assert "bk_scribe.spec" in text
    assert "ISCC.exe" in text
    assert "packaging\\ffmpeg\\bin\\ffmpeg.exe" in text


def test_packaging_spec_is_not_globally_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "*.spec" not in gitignore.splitlines()
    assert "packaging/ffmpeg/bin/ffmpeg.exe" in gitignore
