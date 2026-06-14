# Stage 10 Windows Packaging и мастер первого запуска Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подготовить BK Scribe как устанавливаемое Windows-приложение с per-user установщиком и обязательным мастером первого запуска.

**Architecture:** Stage 10 выполняется двумя отдельными PR. PR 1 добавляет packaging foundation, брендинг BK Scribe, bundled FFmpeg и Inno Setup установщик без изменения пользовательского setup-gate. PR 2 добавляет обязательный wizard поверх существующих `check_readiness`, OBS/FFmpeg/STT/Summary сервисов и блокирует старт рабочего дня, пока локальная настройка не завершена.

**Tech Stack:** Python 3.11+, PySide6, pytest, PyInstaller, Inno Setup, PowerShell build scripts, существующие `StorageService`, `AudioExtractor`, `check_readiness`, `create_transcriber`, `create_summarizer`.

---

## Контекст и границы

Репозиторий: `Damidamida/apps-meeting-day-recorder`.

Базовая ветка для реализации: актуальная `main` после design-spec commit `d19dd6d`.

Рабочие ветки должны использовать префикс `codex/*`. Нельзя push или merge напрямую в `main`.

Текущий этап проекта: Stage 10 `Windows packaging` имеет статус `Сделать`. Реализация Stage 10 не переводит этап в `Готово`; после каждого PR статус остается по правилам `PROJECT_STATE.md` и меняется на `На проверке` только при созданном PR.

Явно исключено из Stage 10:

- автоматическая установка OBS;
- автоматическая настройка OBS без участия пользователя;
- MSI и корпоративное IT-развертывание;
- установка для всех пользователей компьютера;
- admin-install в `Program Files`;
- миграция старых пользовательских данных;
- изменение структуры рабочих дней;
- переименование репозитория и внутренних Python-пакетов;
- переписывание OBS/FFmpeg/STT/Summary pipeline за пределами readiness/setup checks;
- удаление пользовательских данных при uninstall.

Обязательные файлы перед реализацией каждой задачи:

- `AGENTS.md`;
- `PROJECT_STATE.md`;
- `docs/superpowers/specs/2026-06-14-windows-packaging-and-first-run-design.md`;
- этот план.

---

## Разделение будущих PR

### PR 1: Windows-установщик, packaging foundation, bundled FFmpeg, иконка, брендинг

Результат PR 1: пользователь получает русский `.exe`-установщик BK Scribe, который ставит приложение без прав администратора в `%LOCALAPPDATA%\BK Scribe` по умолчанию, позволяет выбрать папку приложения, создает ярлыки, добавляет uninstall, включает app-local `ffmpeg.exe` и не трогает пользовательские настройки или рабочие данные.

Создать:

- `app/branding.py` — единые пользовательские названия, app id, install/display names, пути ресурсов.
- `app/runtime.py` — определение packaged/dev режима, app root, resource root, bundled tool paths.
- `app/assets/README.md` — описание ассетов; в PR 1 сюда копируется пользовательская иконка как `app/assets/bk_scribe.ico`.
- `packaging/pyinstaller/bk_scribe.spec` — сборка `BK Scribe.exe` с app icon, ресурсами и runtime files.
- `packaging/inno/bk_scribe.iss` — русский per-user Inno Setup установщик.
- `packaging/ffmpeg/README.md` — инструкция, куда положить `ffmpeg.exe` для сборки установщика.
- `scripts/build_windows_package.ps1` — единая команда сборки PyInstaller + Inno Setup.
- `tests/test_branding.py` — тесты branding constants.
- `tests/test_runtime.py` — тесты packaged/dev path resolution.

Изменить:

- `app/main.py` — применение `WINDOWS_APP_ID` при запуске Windows.
- `app/ui/main_window.py` — пользовательское название окна и левого меню `BK Scribe`.
- `app/services/audio.py` — `AudioExtractor` использует app-local `ffmpeg.exe`, если он есть.
- `app/services/readiness.py` — FFmpeg readiness проверяет bundled path до системного `PATH`.
- `pyproject.toml` — dev-зависимость `pyinstaller`.
- `.gitignore` — не игнорировать tracked spec в `packaging/pyinstaller/`, продолжить игнорировать build artifacts.
- `README.md` — русская инструкция установки BK Scribe и сборки установщика.
- `PROJECT_STATE.md` — запись о PR 1 и фактических проверках.

Не менять в PR 1:

- мастер первого запуска;
- блокировку старта рабочего дня;
- `config.yaml` пользователя и `.env`;
- пользовательскую папку данных;
- OBS/STT/Summary поведение, кроме выбора bundled FFmpeg.

### PR 2: обязательный мастер первого запуска и setup-gate

Результат PR 2: при первом запуске или незавершенной настройке открывается мастер на русском языке. Пользователь выбирает папку данных с дефолтом `Документы\BK Scribe`, проходит проверки OBS WebSocket, bundled FFmpeg, обязательной транскрипции и обязательных AI-итогов. До успешного мастера кнопка `Начать рабочий день` заблокирована.

Актуальный согласованный план PR 2 вынесен в отдельный документ `docs/superpowers/plans/2026-06-15-first-run-wizard-pr2.md`. Он имеет приоритет над ранними черновыми шагами ниже, потому что после согласования UI было принято:

- делать мастер полноэкранным экраном внутри приложения, а не цепочкой popup-окон;
- убрать верхний блок `Готовность к работе`;
- сделать строгий последовательный порядок шагов без перехода вперед до статуса `Готово`;
- добавить отдельный шаг `AI Tunnel` с одним ключом для транскрипции и AI-итогов;
- сохранить ключ и настройки только после успешных проверок.

Создать:

- `app/services/first_run.py` — dataclass-модель setup state, проверки папки данных, OBS, FFmpeg, транскрипции и AI-итогов.
- `app/ui/first_run_wizard.py` — PySide6 wizard на русском языке.
- `tests/test_first_run.py` — unit-тесты setup state и проверок.
- `tests/test_first_run_ui.py` — UI-тесты wizard и setup-gate.

Изменить:

- `app/config.py` — default-секция `setup` и нормализация статуса мастера.
- `app/services/readiness.py` — публичная проверка FFmpeg с injected command/path для wizard.
- `app/services/summarization.py` — дешевый smoke-test summary на коротком синтетическом тексте без файлов пользователя.
- `app/ui/main_window.py` — показ wizard при первом запуске, блокировка `Начать рабочий день`, повторный запуск wizard из настроек.
- `tests/test_config.py`, `tests/test_readiness.py`, `tests/test_summarization.py`, `tests/test_ui.py` — покрытие setup-gate и smoke checks.
- `README.md` — описание первого запуска для коллег.
- `PROJECT_STATE.md` — запись о PR 2 и фактических проверках.

Не менять в PR 2:

- Inno Setup script, если PR 1 уже принят;
- структуру `MeetingSummaries/YYYY-MM-DD`;
- реальные рабочие записи, transcript и summary пользователя;
- автоматическую установку OBS.

---

## PR 1 Tasks

### Task 1: Branding constants и пользовательское название

**Files:**
- Create: `app/branding.py`
- Modify: `app/main.py`
- Modify: `app/ui/main_window.py`
- Test: `tests/test_branding.py`, `tests/test_ui.py`

- [ ] **Step 1: Write failing branding tests**

Create `tests/test_branding.py`:

```python
from app.branding import (
    APP_DISPLAY_NAME,
    APP_EXECUTABLE_NAME,
    APP_INSTALL_DIR_NAME,
    APP_PUBLISHER,
    WINDOWS_APP_ID,
)


def test_branding_uses_final_user_visible_name() -> None:
    assert APP_DISPLAY_NAME == "BK Scribe"
    assert APP_EXECUTABLE_NAME == "BK Scribe.exe"
    assert APP_INSTALL_DIR_NAME == "BK Scribe"
    assert APP_PUBLISHER == "BK"
    assert WINDOWS_APP_ID == "BK.BKScribe"
```

Add to `tests/test_ui.py`:

```python
def test_main_window_uses_bk_scribe_branding(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)

    window = MainWindow(storage, recorder)

    assert window.windowTitle() == "BK Scribe"
    assert any(label.text() == "BK Scribe" for label in window.findChildren(QLabel))

    window.close()
    app.processEvents()
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_branding.py tests/test_ui.py::test_main_window_uses_bk_scribe_branding -q
```

Expected: fail because `app.branding` does not exist and the window title is still `Meeting Day Recorder`.

- [ ] **Step 3: Add branding constants**

Create `app/branding.py`:

```python
from pathlib import Path


APP_DISPLAY_NAME = "BK Scribe"
APP_EXECUTABLE_NAME = "BK Scribe.exe"
APP_INSTALL_DIR_NAME = "BK Scribe"
APP_PUBLISHER = "BK"
WINDOWS_APP_ID = "BK.BKScribe"
APP_ICON_RESOURCE = Path("app/assets/bk_scribe.ico")
```

- [ ] **Step 4: Apply branding in UI and app startup**

In `app/ui/main_window.py`, import and use `APP_DISPLAY_NAME`:

```python
from app.branding import APP_DISPLAY_NAME
```

Replace `self.setWindowTitle("Meeting Day Recorder")` with:

```python
self.setWindowTitle(APP_DISPLAY_NAME)
```

Replace the left navigation product label text `Meeting Day Recorder` with `APP_DISPLAY_NAME`.

In `app/main.py`, set Windows app id before creating the window:

```python
import sys

from PySide6.QtWidgets import QApplication

from app.branding import WINDOWS_APP_ID
from app.ui.main_window import MainWindow


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:
        return
```

Call `_set_windows_app_id()` at the start of `main()`.

- [ ] **Step 5: Run branding tests**

Run:

```powershell
python -m pytest tests/test_branding.py tests/test_ui.py::test_main_window_uses_bk_scribe_branding -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```powershell
git add app/branding.py app/main.py app/ui/main_window.py tests/test_branding.py tests/test_ui.py
git commit -m "Добавить брендинг BK Scribe"
```

### Task 2: Runtime resource paths и bundled FFmpeg resolver

**Files:**
- Create: `app/runtime.py`
- Modify: `app/services/audio.py`
- Modify: `app/services/readiness.py`
- Test: `tests/test_runtime.py`, `tests/test_audio.py`, `tests/test_readiness.py`

- [ ] **Step 1: Write failing runtime and FFmpeg tests**

Create `tests/test_runtime.py`:

```python
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
    exe = tmp_path / "BK Scribe.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert bundled_tool_path("ffmpeg.exe") == tmp_path / "resources" / "ffmpeg" / "ffmpeg.exe"
```

Add to `tests/test_audio.py`:

```python
def test_audio_extractor_prefers_bundled_ffmpeg(tmp_path: Path) -> None:
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()
    recording_path = meeting_folder / "recording.mkv"
    recording_path.write_text("video", encoding="utf-8")
    bundled_ffmpeg = tmp_path / "resources" / "ffmpeg" / "ffmpeg.exe"
    bundled_ffmpeg.parent.mkdir(parents=True)
    bundled_ffmpeg.write_text("fake exe", encoding="utf-8")

    with (
        patch("app.services.audio.bundled_tool_path", return_value=bundled_ffmpeg),
        patch("app.services.audio.shutil.which", return_value=None),
        patch("app.services.audio.subprocess.run") as run,
    ):
        metadata = AudioExtractor().extract_audio(recording_path, meeting_folder)

    assert metadata["audio_status"] == "extracted"
    assert run.call_args.args[0][0] == str(bundled_ffmpeg)
```

Add to `tests/test_readiness.py`:

```python
def test_readiness_reports_bundled_ffmpeg_before_path(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "resources" / "ffmpeg" / "ffmpeg.exe"
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_text("fake exe", encoding="utf-8")

    with (
        patch("app.services.readiness.bundled_tool_path", return_value=ffmpeg),
        patch("app.services.readiness.shutil.which", return_value=None),
    ):
        statuses = _by_component(check_readiness(_config(), NoopRecorder(), tmp_path))

    assert statuses["Извлечение аудио (FFmpeg)"]["state"] == "ok"
    assert _details(statuses["Извлечение аудио (FFmpeg)"])["Состояние"]["value"] == "Bundled FFmpeg найден"
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_runtime.py tests/test_audio.py::test_audio_extractor_prefers_bundled_ffmpeg tests/test_readiness.py::test_readiness_reports_bundled_ffmpeg_before_path -q
```

Expected: fail because `app.runtime` and bundled resolver do not exist.

- [ ] **Step 3: Implement runtime helpers**

Create `app/runtime.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_root()))
    return base / Path(relative_path)


def bundled_tool_path(filename: str) -> Path:
    return app_root() / "resources" / "ffmpeg" / filename
```

- [ ] **Step 4: Use bundled FFmpeg in audio and readiness**

In `app/services/audio.py`, import resolver:

```python
from app.runtime import bundled_tool_path
```

Add method:

```python
    def _resolve_ffmpeg_command(self) -> str | None:
        bundled = bundled_tool_path("ffmpeg.exe")
        if bundled.is_file():
            return str(bundled)
        if shutil.which(self.ffmpeg_command) is not None:
            return self.ffmpeg_command
        return None
```

Use it in `extract_audio` and pass the resolved command to `subprocess.run`.

In `app/services/readiness.py`, check `bundled_tool_path("ffmpeg.exe").is_file()` before `shutil.which("ffmpeg")`, with user status `Bundled FFmpeg найден`.

- [ ] **Step 5: Run resolver tests**

Run:

```powershell
python -m pytest tests/test_runtime.py tests/test_audio.py tests/test_readiness.py -q
```

Expected: selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add app/runtime.py app/services/audio.py app/services/readiness.py tests/test_runtime.py tests/test_audio.py tests/test_readiness.py
git commit -m "Добавить runtime paths и bundled FFmpeg"
```

### Task 3: PyInstaller и Inno Setup build foundation

**Files:**
- Create: `app/assets/README.md`
- Create: `packaging/pyinstaller/bk_scribe.spec`
- Create: `packaging/inno/bk_scribe.iss`
- Create: `packaging/ffmpeg/README.md`
- Create: `scripts/build_windows_package.ps1`
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Modify: `README.md`

- [ ] **Step 1: Add app assets folder and copy icon in PR 1**

Create `app/assets/README.md`:

```markdown
# Ассеты BK Scribe

В PR 1 сюда копируется пользовательская иконка приложения:

`app/assets/bk_scribe.ico`

Иконка для Stage 10 хранится в репозитории:

`app/assets/bk_scribe.ico`

Иконка используется для окна приложения, `BK Scribe.exe`, ярлыков и установщика.
```

Copy the icon only in PR 1:

```powershell
Copy-Item -LiteralPath "<path-to-icon.ico>" -Destination app\assets\bk_scribe.ico
```

- [ ] **Step 2: Add PyInstaller dependency and ignore rules**

In `pyproject.toml`, add to dev dependencies:

```toml
    "pyinstaller>=6,<7",
```

In `.gitignore`, keep build artifacts out while allowing the tracked spec:

```gitignore
*.spec
!packaging/pyinstaller/*.spec
dist/
build/
packaging/out/
packaging/work/
packaging/ffmpeg/bin/
```

- [ ] **Step 3: Create PyInstaller spec**

Create `packaging/pyinstaller/bk_scribe.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()
ICON = ROOT / "app" / "assets" / "bk_scribe.ico"
FFMPEG = ROOT / "packaging" / "ffmpeg" / "bin" / "ffmpeg.exe"

datas = [(str(ROOT / "app" / "assets"), "app/assets")]
if FFMPEG.is_file():
    datas.append((str(FFMPEG), "resources/ffmpeg"))

a = Analysis(["app/main.py"], pathex=[str(ROOT)], binaries=[], datas=datas, hiddenimports=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BK Scribe",
    debug=False,
    console=False,
    icon=str(ICON),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="BK Scribe")
```

- [ ] **Step 4: Create FFmpeg vendor note and Inno Setup script**

Create `packaging/ffmpeg/README.md`:

```markdown
# FFmpeg для сборки установщика

Установщик BK Scribe должен включать `ffmpeg.exe`, чтобы приложение не зависело от системного `PATH`.

Перед сборкой PR 1 положите Windows binary сюда:

`packaging/ffmpeg/bin/ffmpeg.exe`

Папка `packaging/ffmpeg/bin/` не коммитится. В установщик попадает только содержимое `dist\BK Scribe`, куда PyInstaller копирует этот файл как `resources\ffmpeg\ffmpeg.exe`.
```

Create `packaging/inno/bk_scribe.iss`:

```ini
#define MyAppName "BK Scribe"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "BK"
#define MyAppExeName "BK Scribe.exe"

[Setup]
AppId={{BKScribe-PerUser}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\BK Scribe
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\out
OutputBaseFilename=BK-Scribe-Setup
SetupIconFile=..\..\app\assets\bk_scribe.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
SelectDirDesc=Куда установить BK Scribe?
SelectDirLabel3=Выберите папку для файлов приложения. Рабочие данные выбираются отдельно в мастере первого запуска.

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "..\..\dist\BK Scribe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app\assets\bk_scribe.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app\assets\bk_scribe.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить BK Scribe"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
```

- [ ] **Step 5: Create build script**

Create `scripts/build_windows_package.ps1`:

```powershell
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $repo "app\assets\bk_scribe.ico"
$ffmpeg = Join-Path $repo "packaging\ffmpeg\bin\ffmpeg.exe"
$isccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not (Test-Path -LiteralPath $icon)) {
    throw "Иконка не найдена: $icon"
}
if (-not (Test-Path -LiteralPath $ffmpeg)) {
    throw "FFmpeg не найден: $ffmpeg"
}
if (-not $iscc) {
    throw "Inno Setup 6 не найден. Установите Inno Setup и повторите сборку."
}

Push-Location $repo
try {
    python -m PyInstaller --clean --noconfirm packaging\pyinstaller\bk_scribe.spec
    & $iscc packaging\inno\bk_scribe.iss
}
finally {
    Pop-Location
}
```

- [ ] **Step 6: Run PR 1 verification**

Run:

```powershell
python -m pytest
python -m compileall -q app
.\scripts\build_windows_package.ps1
```

Manual smoke:

- installer UI is Russian;
- install folder can be changed;
- default install folder is `%LOCALAPPDATA%\BK Scribe`;
- Start menu shortcut is `BK Scribe`;
- desktop shortcut is optional;
- `BK Scribe.exe` launches;
- `resources\ffmpeg\ffmpeg.exe` exists in the installed app folder;
- update over the same folder keeps existing `config.yaml`, `.env`, selected data folder and workday data untouched;
- uninstall removes app files but not the selected data folder.

- [ ] **Step 7: Update state and commit**

Add PR 1 results to `PROJECT_STATE.md`, keep Stage 10 out of `Готово`, then commit:

```powershell
git add .gitignore pyproject.toml README.md PROJECT_STATE.md `
  app/branding.py app/main.py app/runtime.py `
  app/ui/main_window.py app/services/audio.py app/services/readiness.py `
  app/assets/README.md app/assets/bk_scribe.ico `
  packaging/ffmpeg/README.md packaging/inno/bk_scribe.iss packaging/pyinstaller/bk_scribe.spec `
  scripts/build_windows_package.ps1 `
  tests/test_branding.py tests/test_runtime.py tests/test_audio.py tests/test_readiness.py tests/test_ui.py
git commit -m "Добавить Windows-упаковку BK Scribe"
```

---

## PR 2 Tasks

### Task 1: Setup config model

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Add to `tests/test_config.py`:

```python
def test_default_setup_requires_first_run_wizard() -> None:
    config = load_config(Path("missing-config.yaml"))

    assert config["setup"] == {
        "completed": False,
        "version": 1,
        "completed_at": "",
        "data_root": "",
        "data_root_checked": False,
        "obs_checked": False,
        "audio_checked": False,
        "aitunnel_checked": False,
        "transcription_checked": False,
        "summary_checked": False,
    }


def test_load_config_normalizes_setup_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
setup:
  completed: yes
  version: 1
  completed_at: 2026-06-14T10:30:00
  data_root: "%USERPROFILE%/Documents/BK Scribe"
  data_root_checked: yes
  obs_checked: yes
  audio_checked: yes
  aitunnel_checked: yes
  transcription_checked: yes
  summary_checked: yes
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["setup"]["completed"] is True
    assert config["setup"]["version"] == 1
    assert config["setup"]["data_root"] == "%USERPROFILE%/Documents/BK Scribe"
    assert config["setup"]["data_root_checked"] is True
    assert config["setup"]["audio_checked"] is True
    assert config["setup"]["aitunnel_checked"] is True
    assert config["setup"]["summary_checked"] is True
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_config.py::test_default_setup_requires_first_run_wizard tests/test_config.py::test_load_config_normalizes_setup_section -q
```

Expected: fail because `setup` config section does not exist.

- [ ] **Step 3: Add setup defaults and normalizer**

In `app/config.py`, add a `setup` section to `DEFAULT_CONFIG`, load it through `_section`, and normalize it:

```python
def _normalize_setup(setup: dict[str, Any]) -> dict[str, Any]:
    return {
        "completed": _safe_bool(setup.get("completed"), False),
        "version": int(setup.get("version") or 1),
        "completed_at": str(setup.get("completed_at") or "").strip(),
        "data_root": str(setup.get("data_root") or "").strip(),
        "data_root_checked": _safe_bool(setup.get("data_root_checked"), False),
        "obs_checked": _safe_bool(setup.get("obs_checked"), False),
        "audio_checked": _safe_bool(setup.get("audio_checked"), False),
        "aitunnel_checked": _safe_bool(setup.get("aitunnel_checked"), False),
        "transcription_checked": _safe_bool(setup.get("transcription_checked"), False),
        "summary_checked": _safe_bool(setup.get("summary_checked"), False),
    }
```

- [ ] **Step 4: Run config tests and commit**

Run:

```powershell
python -m pytest tests/test_config.py -q
```

Expected: config tests pass.

Commit:

```powershell
git add app/config.py tests/test_config.py
git commit -m "Добавить состояние мастера первого запуска"
```

### Task 2: First-run service checks and cheap AI summary smoke

**Files:**
- Create: `app/services/first_run.py`
- Modify: `app/services/summarization.py`
- Test: `tests/test_first_run.py`, `tests/test_summarization.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_first_run.py`:

```python
from pathlib import Path

from app.services.first_run import (
    FirstRunCheck,
    SetupState,
    default_data_root,
    readiness_to_first_run_check,
    setup_completed,
    validate_data_root,
)


def test_default_data_root_uses_documents_bk_scribe(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert default_data_root() == tmp_path / "Documents" / "BK Scribe"


def test_validate_data_root_creates_folder_and_checks_write(tmp_path: Path) -> None:
    target = tmp_path / "BK Scribe"

    check = validate_data_root(target)

    assert check == FirstRunCheck("Папка данных", "ok", "Папка данных доступна для записи.")
    assert target.is_dir()


def test_validate_data_root_rejects_file_path(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    check = validate_data_root(target)

    assert check.state == "error"
    assert "указывает на файл" in check.message


def test_setup_completed_requires_all_checks() -> None:
    state = SetupState(
        data_root="C:/Data",
        data_root_checked=True,
        obs_checked=True,
        audio_checked=True,
        aitunnel_checked=True,
        transcription_checked=True,
        summary_checked=True,
    )

    assert setup_completed(state) is True


def test_readiness_status_maps_to_first_run_check() -> None:
    assert readiness_to_first_run_check(
        {"component": "Запись разговора (OBS)", "state": "ok", "message": "OBS подключен."}
    ) == FirstRunCheck("Запись разговора (OBS)", "ok", "OBS подключен.")
    assert readiness_to_first_run_check(
        {"component": "Транскрипция", "state": "skipped", "message": "Транскрипция не настроена."}
    ).state == "error"
```

Add to `tests/test_summarization.py`:

```python
def test_summary_smoke_test_uses_short_synthetic_text_without_files(monkeypatch) -> None:
    captured = {}

    class Responses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"output_text": "Готово", "usage": {"input_tokens": 10, "output_tokens": 2}})()

    class Client:
        responses = Responses()

    monkeypatch.setenv("AITUNNEL_KEY", "secret")

    result = smoke_test_summary_connection(
        {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "api_key_env": "AITUNNEL_KEY",
            "base_url": "https://api.aitunnel.ru/v1/",
            "env_file": "",
            "timeout_seconds": 120,
        },
        client_factory=lambda **kwargs: Client(),
    )

    assert result["state"] == "ok"
    assert "Короткая тестовая проверка" in captured["input"]
    assert "transcript" not in captured["input"].lower()
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_first_run.py tests/test_summarization.py::test_summary_smoke_test_uses_short_synthetic_text_without_files -q
```

Expected: fail because `app.services.first_run` and `smoke_test_summary_connection` do not exist.

- [ ] **Step 3: Implement first-run service**

Create `app/services/first_run.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FirstRunCheck:
    component: str
    state: str
    message: str


@dataclass(frozen=True)
class SetupState:
    version: int = 1
    data_root: str = ""
    data_root_checked: bool = False
    obs_checked: bool = False
    audio_checked: bool = False
    aitunnel_checked: bool = False
    transcription_checked: bool = False
    summary_checked: bool = False


def default_data_root() -> Path:
    return Path.home() / "Documents" / "BK Scribe"


def validate_data_root(path: Path) -> FirstRunCheck:
    try:
        if path.exists() and not path.is_dir():
            return FirstRunCheck("Папка данных", "error", "Выбранный путь указывает на файл.")
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".bk_scribe_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return FirstRunCheck("Папка данных", "error", "Не удалось записать файл в выбранную папку.")
    return FirstRunCheck("Папка данных", "ok", "Папка данных доступна для записи.")


def readiness_to_first_run_check(status: dict[str, object]) -> FirstRunCheck:
    component = str(status.get("component") or "Проверка")
    state = str(status.get("state") or "error")
    message = str(status.get("message") or "Проверка не выполнена.")
    return FirstRunCheck(component, "ok" if state == "ok" else "error", message)


def setup_completed(state: SetupState) -> bool:
    return all(
        [
            bool(state.data_root.strip()),
            state.data_root_checked,
            state.obs_checked,
            state.audio_checked,
            state.aitunnel_checked,
            state.transcription_checked,
            state.summary_checked,
        ]
    )
```

- [ ] **Step 4: Implement cheap summary smoke test**

In `app/services/summarization.py`, add:

```python
def smoke_test_summary_connection(
    config: dict[str, Any],
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not config.get("enabled", False):
        return {"state": "error", "message": "AI-итоги выключены."}
    api_key = load_api_key(
        str(config.get("api_key_env") or SUMMARY_API_KEY_ENV_DEFAULT),
        config.get("env_file") or "",
    )
    if not api_key:
        return {"state": "error", "message": "Ключ не найден."}
    factory = client_factory or OpenAISummarizer._default_client_factory
    try:
        client = factory(
            api_key=api_key,
            base_url=str(config.get("base_url") or None) or None,
            timeout=float(config.get("timeout_seconds") or 120),
        )
        response = client.responses.create(
            model=str(config.get("model") or "gpt-5.4-mini"),
            instructions="Ответь одним коротким предложением на русском языке.",
            input="Короткая тестовая проверка BK Scribe: ответь, что AI-итоги готовы.",
        )
        text = extract_response_text(response)
    except Exception:
        return {"state": "error", "message": "Сервис временно недоступен."}
    if not text:
        return {"state": "error", "message": "Сервис не вернул текст."}
    return {"state": "ok", "message": "AI-итоги готовы."}
```

- [ ] **Step 5: Run service tests and commit**

Run:

```powershell
python -m pytest tests/test_first_run.py tests/test_summarization.py -q
```

Expected: selected tests pass.

Commit:

```powershell
git add app/services/first_run.py app/services/summarization.py tests/test_first_run.py tests/test_summarization.py
git commit -m "Добавить проверки мастера первого запуска"
```

### Task 3: PySide6 wizard and MainWindow setup-gate

**Files:**
- Create: `app/ui/first_run_wizard.py`
- Modify: `app/ui/main_window.py`
- Test: `tests/test_first_run_ui.py`, `tests/test_ui.py`

- [ ] **Step 1: Write failing wizard and gate tests**

Create `tests/test_first_run_ui.py`:

```python
from pathlib import Path

from PySide6.QtWidgets import QApplication, QPushButton

from app.services.first_run import FirstRunCheck
from app.ui.first_run_wizard import FirstRunWizard


def test_first_run_wizard_starts_with_default_data_folder(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("app.ui.first_run_wizard.default_data_root", lambda: tmp_path / "Documents" / "BK Scribe")

    wizard = FirstRunWizard(config={})

    assert wizard.data_root_input.text() == str(tmp_path / "Documents" / "BK Scribe")
    assert wizard.finish_button.text() == "Начать работу"
    assert wizard.finish_button.isEnabled() is False

    wizard.close()
    app.processEvents()


def test_first_run_wizard_enables_finish_after_all_checks() -> None:
    app = QApplication.instance() or QApplication([])
    wizard = FirstRunWizard(config={})

    for component in ("data_root", "obs", "audio", "aitunnel", "transcription", "summary"):
        wizard.set_check_result(component, FirstRunCheck(component, "ok", "Готово"))

    assert wizard.finish_button.isEnabled() is True

    wizard.close()
    app.processEvents()


def test_first_run_wizard_has_required_check_buttons() -> None:
    app = QApplication.instance() or QApplication([])
    wizard = FirstRunWizard(config={})

    button_texts = {button.text() for button in wizard.findChildren(QPushButton)}

    assert "Проверить OBS" in button_texts
    assert "Проверить аудио" in button_texts
    assert "Проверить транскрипцию" in button_texts
    assert "Проверить AI-итоги" in button_texts

    wizard.close()
    app.processEvents()
```

Add to `tests/test_ui.py`:

```python
def test_start_workday_is_blocked_until_first_run_setup_completed(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    opened = []

    window = MainWindow(storage, recorder)
    monkeypatch.setattr(window, "open_first_run_wizard", lambda: opened.append(True))
    window.config["setup"]["completed"] = False
    window.refresh_buttons()
    window.start_workday()

    assert storage.workday_active is False
    assert opened == [True]
    assert "Завершите мастер настройки" in window.status_label.text()

    window.close()
    app.processEvents()
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_first_run_ui.py tests/test_ui.py::test_start_workday_is_blocked_until_first_run_setup_completed -q
```

Expected: fail because wizard and setup-gate do not exist.

- [ ] **Step 3: Create wizard UI**

Create `app/ui/first_run_wizard.py` with an embedded fullscreen `FirstRunWizard(QWidget)` page. It is added to the main window page container instead of being launched as a modal dialog:

```python
class FirstRunWizard(QWidget):
    config_changed = Signal(dict)
    completed = Signal(dict)

    def __init__(
        self,
        config: dict,
        state: FirstRunState | dict[str, Any],
        recorder: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.state = state if isinstance(state, FirstRunState) else normalize_setup_config(state)
        self.recorder = recorder
        self.step_buttons: dict[str, QPushButton] = {}
        self.step_pages: dict[str, QWidget] = {}
        self._build_ui()
        self.refresh()
```

Add `choose_data_root`, per-step check handlers, `open_step`, `go_next`, `go_back`, `finish_setup` and `refresh` methods. The wizard emits `config_changed` after successful step checks and emits `completed(config)` only when all required steps are `ok`.

- [ ] **Step 4: Add setup gate in `MainWindow`**

Import wizard:

```python
from app.ui.first_run_wizard import FirstRunWizard
```

Add helpers:

```python
    def _setup_completed(self) -> bool:
        return bool(self.config.get("setup", {}).get("completed", False))

    def open_first_run_wizard(self) -> None:
        self.pages.setCurrentWidget(self.first_run_wizard)
```

At the start of `start_workday`:

```python
        if not self._setup_completed():
            self.status_label.setText("Завершите мастер настройки BK Scribe перед началом рабочего дня.")
            self.open_first_run_wizard()
            return
```

- [ ] **Step 5: Run wizard and gate tests**

Run:

```powershell
python -m pytest tests/test_first_run_ui.py tests/test_ui.py::test_start_workday_is_blocked_until_first_run_setup_completed -q
```

Expected: selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add app/ui/first_run_wizard.py app/ui/main_window.py tests/test_first_run_ui.py tests/test_ui.py
git commit -m "Добавить мастер и блокировку первого запуска"
```

### Task 4: Persist setup completion, data folder and settings entry

**Files:**
- Modify: `app/config.py`
- Modify: `app/ui/main_window.py`
- Test: `tests/test_config.py`, `tests/test_ui.py`

- [ ] **Step 1: Write failing persistence and settings tests**

Add to `tests/test_ui.py`:

```python
def test_first_run_completion_updates_config_storage_root_and_local_file(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "old", recorder)
    config_path = tmp_path / "config.yaml"
    monkeypatch.chdir(tmp_path)

    window = MainWindow(storage, recorder)
    data_root = tmp_path / "Documents" / "BK Scribe"

    window._finish_first_run_setup({"data_root": str(data_root)})

    assert window.config["setup"]["completed"] is True
    assert window.config["storage"]["root"] == str(data_root)
    assert window.storage.root == data_root
    assert "completed: true" in config_path.read_text(encoding="utf-8")

    window.close()
    app.processEvents()


def test_settings_contains_setup_wizard_button(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)

    window = MainWindow(storage, recorder)
    window.open_settings()

    buttons = {button.text() for button in window.findChildren(QPushButton)}
    assert "Открыть мастер настройки" in buttons

    window.close()
    app.processEvents()
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_ui.py::test_first_run_completion_updates_config_storage_root_and_local_file tests/test_ui.py::test_settings_contains_setup_wizard_button -q
```

Expected: fail because persistence and settings entry are missing.

- [ ] **Step 3: Add config save helper**

In `app/config.py`, add:

```python
def save_config(config: dict[str, Any], path: Path = Path("config.yaml")) -> None:
    serializable = deepcopy(config)
    serializable.pop("_warnings", None)
    with path.open("w", encoding="utf-8") as config_file:
        yaml.safe_dump(serializable, config_file, allow_unicode=True, sort_keys=False)
```

- [ ] **Step 4: Persist setup completion and add settings button**

In `app/ui/main_window.py`, implement:

```python
    def _finish_first_run_setup(self, payload: dict) -> None:
        data_root = str(payload.get("data_root") or "").strip()
        self.config["storage"]["root"] = data_root
        self.config["setup"] = {
            **self.config.get("setup", {}),
            "completed": True,
            "version": CURRENT_SETUP_VERSION,
            "completed_at": datetime.now().isoformat(),
            "data_root": data_root,
            "data_root_checked": True,
            "obs_checked": True,
            "audio_checked": True,
            "aitunnel_checked": True,
            "transcription_checked": True,
            "summary_checked": True,
        }
        save_config(self.config)
        self.storage.root = Path(data_root)
        self.storage.load_today_state()
        self.status_label.setText("Мастер настройки завершен. Можно начинать рабочий день.")
        self.refresh_buttons()
```

In the settings page, add button `Открыть мастер настройки` wired to `open_first_run_wizard`.

- [ ] **Step 5: Run persistence tests and commit**

Run:

```powershell
python -m pytest tests/test_config.py tests/test_ui.py::test_first_run_completion_updates_config_storage_root_and_local_file tests/test_ui.py::test_settings_contains_setup_wizard_button -q
```

Expected: selected tests pass.

Commit:

```powershell
git add app/config.py app/ui/main_window.py tests/test_config.py tests/test_ui.py
git commit -m "Сохранять результат мастера первого запуска"
```

### Task 5: PR 2 verification and docs/state

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_STATE.md`

- [ ] **Step 1: Run full verification**

Run:

```powershell
python -m pytest
python -m compileall -q app
```

Expected: full suite passes and compileall succeeds.

- [ ] **Step 2: Manual wizard smoke test**

Run:

```powershell
python -m app.main
```

Manual checks:

- clean profile with no `config.yaml` opens `Мастер настройки BK Scribe`;
- default data folder is `Документы\BK Scribe`;
- data folder can be changed and write-checked;
- OBS check explains how to start OBS and enable WebSocket when unavailable;
- FFmpeg check finds bundled `resources\ffmpeg\ffmpeg.exe` in installed build;
- transcription cannot be skipped;
- summary cannot be skipped;
- summary smoke test sends only the short synthetic test text;
- `Начать рабочий день` stays blocked until all required checks are ok;
- after wizard completion, `config.yaml` contains no secret values and stores only config paths/status;
- existing `.env`, user data folder, workdays, recordings, audio, transcript and summary files are not modified by wizard checks.

- [ ] **Step 3: Update README and `PROJECT_STATE.md`**

README section `Первый запуск BK Scribe` must state:

- wizard opens automatically before first workday;
- data folder default is `Документы\BK Scribe`;
- OBS must be installed and WebSocket enabled by the user;
- FFmpeg is bundled in installed app;
- transcription and AI-итоги are mandatory for colleagues;
- AI summary test uses a short synthetic text and does not use real recordings/transcript/summary.

`PROJECT_STATE.md` entry must mention PR 2, exact test results, manual smoke results, and that Stage 10 is not `Готово` until user acceptance.

- [ ] **Step 4: Commit**

```powershell
git add README.md PROJECT_STATE.md
git commit -m "Обновить документацию мастера первого запуска"
```

---

## Final verification before each PR

Run before opening PR 1 or PR 2:

```powershell
git status --short
python -m pytest
python -m compileall -q app
git diff --stat origin/main...HEAD
```

For PR 1 also run:

```powershell
.\scripts\build_windows_package.ps1
```

Expected:

- no staged `config.yaml`, `.env`, recordings, audio, transcript, summary or personal data;
- no push or merge to `main`;
- all user-facing text is Russian;
- local-first behavior is preserved;
- PR title/body are Russian;
- PR body lists exact verification results.

Suggested PR 1 title:

```text
Добавить Windows-установщик BK Scribe
```

Suggested PR 2 title:

```text
Добавить мастер первого запуска BK Scribe
```

## Self-review checklist

- Spec coverage: PR 1 covers installer, packaging foundation, bundled FFmpeg, icon, BK Scribe branding, shortcuts, uninstall and update without data loss; PR 2 covers required wizard, data folder, OBS, FFmpeg, transcription, AI summary and workday blocking.
- Placeholder scan: forbidden unfinished markers are absent.
- Type consistency: `FirstRunCheck`, `SetupState`, `default_data_root`, `validate_data_root`, `setup_completed`, `smoke_test_summary_connection`, `app_root`, `resource_path` and `bundled_tool_path` are introduced before use.
- Scope control: the plan does not implement OBS installation, admin install, MSI, user-data migration, repository rename, or pipeline rewrites outside readiness/setup checks.
