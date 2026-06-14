# Красивый просмотр итогов и единый файл summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести `Ревью` и `Архив` на красивый просмотр итогов по умолчанию, редактирование по кнопке и единственные итоговые файлы `summary.md` / `00_day_summary.md`.

**Architecture:** Сначала обновляется storage-слой и генерация итогов, чтобы у UI был один источник данных. Затем добавляется reusable widget для Markdown-просмотра/редактирования и он подключается в `Ревью` и `Архив`. Старые draft/final файлы читаются только как fallback, новые записи идут только в новую модель.

**Tech Stack:** Python, PySide6, pytest, существующие `StorageService`, `MainWindow`, `app.services.archive`, `app.services.summarization`.

---

## Scope и база

Работать в отдельной ветке `codex/rendered-summary-single-file` от актуальной `main`.

Перед началом:

```powershell
git switch main
git pull --ff-only
git switch -c codex/rendered-summary-single-file
Get-Content AGENTS.md -Encoding UTF8
Get-Content PROJECT_STATE.md -Encoding UTF8
```

Подтвердить:

- репозиторий: `D:\MeetingsApp\stable\apps-meeting-day-recorder`;
- базовая ветка: `main`;
- текущий этап: этап 8 остается `На проверке`;
- вне scope: история версий, diff, backup, миграция старых тестовых встреч, календарь Архива, удаление старых пользовательских файлов.

## File map

- Modify: `app/services/storage.py`
  - добавить новые пути и методы `summary.md` / `00_day_summary.md`;
  - оставить fallback-чтение старых файлов;
  - перевести pipeline на новые пути.
- Modify: `app/services/summarization.py`
  - итог встречи и итог дня должны записываться в новые файлы и metadata.
- Modify: `app/services/archive.py`
  - поиск должен читать новые файлы и fallback старых файлов.
- Create: `app/ui/summary_viewer.py`
  - reusable виджет красивого Markdown-просмотра и редактирования.
- Modify: `app/ui/main_window.py`
  - заменить старый editor-first UI в `Ревью`;
  - заменить editor-first UI в `Архиве`;
  - удалить кнопки старой модели.
- Modify: `tests/test_storage.py`
  - покрыть новую файловую модель.
- Modify: `tests/test_summarization.py`
  - покрыть новые output paths генерации.
- Modify: `tests/test_archive.py`
  - покрыть поиск по новым файлам.
- Modify: `tests/test_ui.py`
  - покрыть новый режим просмотра/редактирования в `Ревью` и `Архиве`.
- Modify: `PROJECT_STATE.md`
  - записать статус, изменения, проверки и следующий шаг.

## Task 1: Storage API для единственных итоговых файлов

**Files:**
- Modify: `app/services/storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Add tests near the existing summary storage tests:

```python
def test_read_and_save_meeting_summary_single_file_with_legacy_fallback(tmp_path) -> None:
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    meeting_folder = storage.create_meeting_folder("Новая модель", datetime(2026, 6, 14, 10, 0))

    (meeting_folder / "summary_draft.md").write_text("# Старый итог\n", encoding="utf-8")
    assert storage.read_meeting_summary(meeting_folder) == "# Старый итог\n"

    saved_path = storage.save_meeting_summary(meeting_folder, "# Новый итог\n")

    assert saved_path == meeting_folder / "summary.md"
    assert storage.read_meeting_summary(meeting_folder) == "# Новый итог\n"
    assert (meeting_folder / "summary.md").read_text(encoding="utf-8") == "# Новый итог\n"


def test_read_and_save_day_summary_single_file_with_legacy_fallback(tmp_path) -> None:
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date(2026, 6, 14))

    (day_folder / "00_day_summary_draft.md").write_text("# Старый итог дня\n", encoding="utf-8")
    assert storage.read_day_summary(day_folder) == "# Старый итог дня\n"

    saved_path = storage.save_day_summary(day_folder, "# Новый итог дня\n")

    assert saved_path == day_folder / "00_day_summary.md"
    assert storage.read_day_summary(day_folder) == "# Новый итог дня\n"
```

Make sure the imports already include `datetime` and `date`; if `date` is missing, add:

```python
from datetime import date, datetime
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_storage.py::test_read_and_save_meeting_summary_single_file_with_legacy_fallback tests/test_storage.py::test_read_and_save_day_summary_single_file_with_legacy_fallback -q
```

Expected: both tests fail because `read_meeting_summary`, `save_meeting_summary`, `read_day_summary`, `save_day_summary` do not exist.

- [ ] **Step 3: Implement storage methods**

In `StorageService`, add helper paths and methods near the existing draft/final methods:

```python
    def meeting_summary_path(self, meeting_folder: Path) -> Path:
        return Path(meeting_folder) / "summary.md"

    def day_summary_path(self, day_folder: Path) -> Path:
        return Path(day_folder) / "00_day_summary.md"

    def read_meeting_summary(self, meeting_folder: Path) -> str:
        folder = Path(meeting_folder)
        primary = self.meeting_summary_path(folder)
        if primary.is_file():
            return self._read_or_create_text(primary, self._meeting_summary_placeholder())
        legacy_draft = folder / "summary_draft.md"
        if legacy_draft.is_file():
            return self._read_or_create_text(legacy_draft, self._meeting_summary_placeholder())
        legacy_final = folder / "summary_final.md"
        if legacy_final.is_file():
            return self._read_or_create_text(legacy_final, self._meeting_summary_placeholder())
        return self._read_or_create_text(primary, self._meeting_summary_placeholder())

    def save_meeting_summary(self, meeting_folder: Path, content: str) -> Path:
        return self._write_text(self.meeting_summary_path(Path(meeting_folder)), content)

    def read_day_summary(self, day_folder: Path) -> str:
        folder = Path(day_folder)
        primary = self.day_summary_path(folder)
        if primary.is_file():
            return self._read_or_create_text(primary, self._day_summary_placeholder())
        legacy_draft = folder / "00_day_summary_draft.md"
        if legacy_draft.is_file():
            return self._read_or_create_text(legacy_draft, self._day_summary_placeholder())
        legacy_final = folder / "00_day_summary_final.md"
        if legacy_final.is_file():
            return self._read_or_create_text(legacy_final, self._day_summary_placeholder())
        return self._read_or_create_text(primary, self._day_summary_placeholder())

    def save_day_summary(self, day_folder: Path, content: str) -> Path:
        return self._write_text(self.day_summary_path(Path(day_folder)), content)
```

Keep existing draft/final methods for compatibility until all callers and tests are migrated.

- [ ] **Step 4: Run storage tests**

Run:

```powershell
python -m pytest tests/test_storage.py::test_read_and_save_meeting_summary_single_file_with_legacy_fallback tests/test_storage.py::test_read_and_save_day_summary_single_file_with_legacy_fallback -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/services/storage.py tests/test_storage.py
git commit -m "Добавить единственные файлы итогов"
```

## Task 2: Перевести генерацию итогов и metadata на новые пути

**Files:**
- Modify: `app/services/storage.py`
- Modify: `app/services/summarization.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_summarization.py`

- [ ] **Step 1: Write failing tests for generated paths**

Update or add tests so successful generation writes new files:

```python
def test_successful_summary_pipeline_writes_single_summary_file(tmp_path: Path, monkeypatch) -> None:
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    meeting_folder = storage.create_meeting_folder("Итог", datetime(2026, 6, 14, 11, 0))
    (meeting_folder / "transcript.md").write_text("Обсудили план", encoding="utf-8")

    monkeypatch.setattr(
        "app.services.summarization.SummarizationService.generate_meeting_summary",
        lambda self, folder, progress_callback=None: {
            "summary_status": "draft_created",
            "summary_path": str(Path(folder) / "summary.md"),
            "summary_generated_at": "2026-06-14T11:30:00",
        },
    )

    metadata = storage.process_meeting_pipeline(meeting_folder)

    assert metadata["summary_path"] == str(meeting_folder / "summary.md")
```

For day summary, update the existing successful day summary test expectation:

```python
assert metadata["day_summary_path"] == str(tmp_path / "00_day_summary.md")
assert "Сводка дня" in (tmp_path / "00_day_summary.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_storage.py tests/test_summarization.py -q
```

Expected: failures still mention `summary_draft.md` or `00_day_summary_draft.md`.

- [ ] **Step 3: Update summarization output paths**

In `app/services/summarization.py`, replace generated meeting summary path:

```python
summary_path = meeting_folder / "summary.md"
```

Replace generated day summary path:

```python
summary_path = day_folder / "00_day_summary.md"
```

Keep metadata keys as `summary_path` and `day_summary_path`; only their values change.

- [ ] **Step 4: Update storage pipeline reads**

In `StorageService.process_day_summary_pipeline`, replace:

```python
current_summary = self.read_day_summary_draft(day_folder)
```

with:

```python
current_summary = self.read_day_summary(day_folder)
```

In any code that checks whether a meeting summary exists for pipeline inclusion, prefer:

```python
summary_path = Path(str(metadata.get("summary_path") or self.meeting_summary_path(meeting_folder)))
```

If old metadata points to `summary_draft.md`, keep reading that path as fallback. New metadata from new runs must point to `summary.md`.

- [ ] **Step 5: Update user-facing pipeline messages**

Replace user-facing strings:

```python
"summary_draft.md готов к ревью."
"00_day_summary_draft.md готов."
```

with:

```python
"summary.md готов к ревью."
"00_day_summary.md готов."
```

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m pytest tests/test_storage.py tests/test_summarization.py -q
```

Expected: tests pass after updating old assertions to the new filenames where new behavior is expected.

- [ ] **Step 7: Commit**

```powershell
git add app/services/storage.py app/services/summarization.py tests/test_storage.py tests/test_summarization.py
git commit -m "Перевести генерацию итогов на новую файловую модель"
```

## Task 3: Markdown preview/edit widget

**Files:**
- Create: `app/ui/summary_viewer.py`
- Modify: `tests/test_ui.py`

- [ ] **Step 1: Write widget tests**

Add tests for a reusable widget:

```python
def test_summary_material_view_starts_in_preview_mode() -> None:
    app = QApplication.instance() or QApplication([])
    view = SummaryMaterialView("Итоги встречи")
    view.set_markdown("# Итоги встречи\n\n## Кратко\n- Обсудили релиз")

    assert view.mode == "preview"
    assert view.editor.isHidden()
    assert view.preview.isVisible()
    assert view.edit_button.isVisible()
    assert view.save_button.isHidden()
    assert view.cancel_button.isHidden()
    assert "Обсудили релиз" in view.preview.toPlainText()


def test_summary_material_view_edit_save_and_cancel_signals() -> None:
    app = QApplication.instance() or QApplication([])
    saved: list[str] = []
    view = SummaryMaterialView("Итоги встречи")
    view.save_requested.connect(saved.append)
    view.set_markdown("# Старый итог\n")

    view.enter_edit_mode()
    view.editor.setPlainText("# Новый итог\n")
    view.save_button.click()

    assert saved == ["# Новый итог\n"]
    assert view.mode == "preview"

    view.enter_edit_mode()
    view.editor.setPlainText("# Несохраненный итог\n")
    view.cancel_button.click()

    assert view.markdown == "# Новый итог\n"
    assert view.mode == "preview"
```

Import the widget in `tests/test_ui.py`:

```python
from app.ui.summary_viewer import SummaryMaterialView
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_ui.py::test_summary_material_view_starts_in_preview_mode tests/test_ui.py::test_summary_material_view_edit_save_and_cancel_signals -q
```

Expected: import fails because `app.ui.summary_viewer` does not exist.

- [ ] **Step 3: Implement `SummaryMaterialView`**

Create `app/ui/summary_viewer.py`:

```python
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class SummaryMaterialView(QWidget):
    save_requested = Signal(str)
    cancel_requested = Signal()
    edit_requested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = "preview"
        self.markdown = ""

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.edit_button = QPushButton("Редактировать")
        self.save_button = QPushButton("Сохранить")
        self.cancel_button = QPushButton("Отмена")
        header.addWidget(self.edit_button)
        header.addWidget(self.save_button)
        header.addWidget(self.cancel_button)
        layout.addLayout(header)

        self.preview = QTextBrowser()
        self.preview.setObjectName("summaryPreview")
        self.preview.setOpenExternalLinks(False)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("summaryEditor")

        layout.addWidget(self.preview, 1)
        layout.addWidget(self.editor, 1)
        self.setLayout(layout)

        self.edit_button.clicked.connect(self.enter_edit_mode)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self._cancel)
        self._sync_mode()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_markdown(self, markdown: str) -> None:
        self.markdown = markdown
        self.editor.setPlainText(markdown)
        self.preview.setMarkdown(markdown)
        self.mode = "preview"
        self._sync_mode()

    def enter_edit_mode(self) -> None:
        self.mode = "edit"
        self.editor.setPlainText(self.markdown)
        self.edit_requested.emit()
        self._sync_mode()

    def _save(self) -> None:
        self.markdown = self.editor.toPlainText()
        self.preview.setMarkdown(self.markdown)
        self.mode = "preview"
        self._sync_mode()
        self.save_requested.emit(self.markdown)

    def _cancel(self) -> None:
        self.editor.setPlainText(self.markdown)
        self.mode = "preview"
        self._sync_mode()
        self.cancel_requested.emit()

    def _sync_mode(self) -> None:
        editing = self.mode == "edit"
        self.preview.setVisible(not editing)
        self.editor.setVisible(editing)
        self.edit_button.setVisible(not editing)
        self.save_button.setVisible(editing)
        self.cancel_button.setVisible(editing)
```

- [ ] **Step 4: Run widget tests**

Run:

```powershell
python -m pytest tests/test_ui.py::test_summary_material_view_starts_in_preview_mode tests/test_ui.py::test_summary_material_view_edit_save_and_cancel_signals -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/ui/summary_viewer.py tests/test_ui.py
git commit -m "Добавить виджет просмотра итогов"
```

## Task 4: Подключить новый просмотр в Ревью

**Files:**
- Modify: `app/ui/main_window.py`
- Modify: `tests/test_ui.py`

- [ ] **Step 1: Write failing Review UI tests**

Add tests:

```python
def test_review_summary_opens_as_rendered_preview_and_removes_old_buttons(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date.today())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder("Ревью", datetime.fromisoformat(f"{day_folder.name}T10:00:00"))
    storage.save_meeting_summary(meeting, "# Итоги встречи\n\n## Кратко\n- Готово")

    window = MainWindow(storage, recorder)
    window.open_review()
    window.load_selected_meeting(meeting)

    page = window.pages.widget(1)
    button_texts = [button.text() for button in page.findChildren(QPushButton)]

    assert "Сохранить черновики" not in button_texts
    assert "Сохранить финальные файлы" not in button_texts
    assert "Открыть папку дня" not in button_texts
    assert window.review_summary_view.mode == "preview"
    assert window.review_summary_view.preview.isVisible()
    assert window.review_summary_view.editor.isHidden()


def test_review_summary_edit_saves_single_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date.today())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder("Ревью", datetime.fromisoformat(f"{day_folder.name}T10:00:00"))
    storage.save_meeting_summary(meeting, "# Старый итог\n")

    window = MainWindow(storage, recorder)
    window.open_review()
    window.load_selected_meeting(meeting)
    window.review_summary_view.enter_edit_mode()
    window.review_summary_view.editor.setPlainText("# Новый итог\n")
    window.review_summary_view.save_button.click()

    assert (meeting / "summary.md").read_text(encoding="utf-8") == "# Новый итог\n"
    assert window.review_summary_view.mode == "preview"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_ui.py::test_review_summary_opens_as_rendered_preview_and_removes_old_buttons tests/test_ui.py::test_review_summary_edit_saves_single_file -q
```

Expected: tests fail because `review_summary_view` is not connected and old buttons still exist.

- [ ] **Step 3: Replace Review editor tab content**

In `_create_review_page`, replace the first tab widget content:

```python
self.review_summary_view = SummaryMaterialView("Итоги встречи")
self.review_summary_view.save_requested.connect(self.save_review_summary)
self.meeting_transcript_editor = QTextBrowser()
...
self.review_tabs.addTab(self.review_summary_view, "Итоги встречи")
self.review_tabs.addTab(self.meeting_transcript_editor, "Транскрипт")
```

Remove creation and use of:

```python
self.meeting_summary_editor = QPlainTextEdit()
self.day_summary_editor = self.meeting_summary_editor
self.save_drafts_button
self.save_final_files_button
```

Remove the action row buttons:

```python
"Сохранить черновики"
"Сохранить финальные файлы"
"Открыть папку дня"
```

- [ ] **Step 4: Add Review save method**

Add:

```python
def save_review_summary(self, content: str) -> None:
    if self.review_day_summary_selected:
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            return
        self.storage.save_day_summary(day_folder, content)
        self.status_label.setText("Итог дня сохранен локально.")
        self.review_summary_view.set_markdown(self.storage.read_day_summary(day_folder))
        return

    if self.selected_review_meeting_folder is None:
        return
    self.storage.save_meeting_summary(self.selected_review_meeting_folder, content)
    self.status_label.setText("Итог встречи сохранен локально.")
    self.review_summary_view.set_markdown(
        self.storage.read_meeting_summary(self.selected_review_meeting_folder)
    )
```

Update `load_selected_meeting`:

```python
self.review_summary_view.set_title("Итоги встречи")
self.review_summary_view.set_markdown(self.storage.read_meeting_summary(meeting_folder))
```

Update `load_day_summary_review`:

```python
self.review_summary_view.set_title("Итоги дня")
self.review_summary_view.set_markdown(self.storage.read_day_summary(day_folder))
```

- [ ] **Step 5: Remove or adapt old save methods**

Keep `save_drafts` and `save_final_files` only if other code still calls them, but make them private compatibility wrappers with no visible buttons:

```python
def save_drafts(self) -> None:
    self.save_review_summary(self.review_summary_view.markdown)

def save_final_files(self) -> None:
    self.save_review_summary(self.review_summary_view.markdown)
```

If no caller remains after tests are updated, delete these methods.

- [ ] **Step 6: Run Review tests**

Run:

```powershell
python -m pytest tests/test_ui.py::test_review_summary_opens_as_rendered_preview_and_removes_old_buttons tests/test_ui.py::test_review_summary_edit_saves_single_file -q
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```powershell
git add app/ui/main_window.py tests/test_ui.py
git commit -m "Перевести Ревью на просмотр итогов"
```

## Task 5: Подключить новый просмотр в Архиве

**Files:**
- Modify: `app/ui/main_window.py`
- Modify: `tests/test_ui.py`

- [ ] **Step 1: Write failing Archive UI tests**

Add tests:

```python
def test_archive_day_card_is_clickable_without_open_button(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})

    window = MainWindow(storage, recorder)
    window.open_archive()

    day_buttons = [button.text() for button in window.archive_days_list.findChildren(QPushButton)]

    assert "Открыть" not in day_buttons
    assert window.selected_archive_day_folder == day_folder


def test_archive_summary_card_opens_preview_and_saves_single_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T10:00:00"),
    )
    storage.save_meeting_summary(meeting, "# Старый итог\n")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.open_archive_meeting_summary(meeting)
    window.archive_summary_view.enter_edit_mode()
    window.archive_summary_view.editor.setPlainText("# Новый итог\n")
    window.archive_summary_view.save_button.click()

    assert (meeting / "summary.md").read_text(encoding="utf-8") == "# Новый итог\n"
    assert window.archive_summary_view.mode == "preview"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_ui.py::test_archive_day_card_is_clickable_without_open_button tests/test_ui.py::test_archive_summary_card_opens_preview_and_saves_single_file -q
```

Expected: old `Открыть` button exists and `archive_summary_view` is not connected.

- [ ] **Step 3: Replace Archive editor state**

In `_create_archive_page`, create:

```python
self.archive_summary_view = SummaryMaterialView("Итоги")
self.archive_summary_view.save_requested.connect(self.save_archive_summary)
self.archive_open_material: tuple[str, Path] | None = None
self.archive_material_mode = "summary"
```

Remove or stop using:

```python
self.archive_editor = QPlainTextEdit()
self.save_archive_draft()
self.save_archive_final()
```

- [ ] **Step 4: Make day cards clickable**

In `_create_archive_day_card`, remove the button row with `Открыть`.

Use the existing card click pattern from Review/Workday. If `_create_card` does not emit clicks, use the existing clickable card class already used for meeting cards. The day card must call:

```python
self.select_archive_day(day.folder)
```

Add selected state object name or property:

```python
card.setProperty("selected", day.folder == self.selected_archive_day_folder)
```

Use the existing style refresh helper if the app already repolishes selected cards.

- [ ] **Step 5: Open only one archive material**

Add methods:

```python
def open_archive_day_summary(self, day_folder: Path) -> None:
    self.selected_archive_day_folder = day_folder
    self.archive_open_material = ("day_summary", day_folder)
    self.archive_material_mode = "summary"
    self._render_archive_detail()

def open_archive_meeting_summary(self, meeting_folder: Path) -> None:
    self.selected_archive_meeting_folder = meeting_folder
    self.archive_open_material = ("meeting_summary", meeting_folder)
    self.archive_material_mode = "summary"
    self._render_archive_detail()

def open_archive_meeting_transcript(self, meeting_folder: Path) -> None:
    self.selected_archive_meeting_folder = meeting_folder
    self.archive_open_material = ("meeting_summary", meeting_folder)
    self.archive_material_mode = "transcript"
    self._render_archive_detail()
```

Update search open behavior:

```python
if match.meeting_folder is not None:
    if match.kind == "Транскрипт":
        self.open_archive_meeting_transcript(match.meeting_folder)
    else:
        self.open_archive_meeting_summary(match.meeting_folder)
elif match.kind == "Итоги дня":
    self.open_archive_day_summary(match.day_folder)
else:
    self.select_archive_day(match.day_folder)
```

- [ ] **Step 6: Render opened cards with header actions**

In `_create_archive_day_summary_card`, if `self.archive_open_material == ("day_summary", day.folder)`, add `self.archive_summary_view` inside the card and set:

```python
self.archive_summary_view.set_title("Итоги дня")
self.archive_summary_view.set_markdown(self.storage.read_day_summary(day.folder))
```

Header actions for preview mode:

```python
"Редактировать"
"Обновить итоги дня"
"Завершить день"  # only when day.metadata.get("status") == "active"
```

In edit mode, only:

```python
"Сохранить"
"Отмена"
```

In `_create_archive_meeting_card`, if opened, set:

```python
self.archive_summary_view.set_title("Итоги встречи")
self.archive_summary_view.set_markdown(self.storage.read_meeting_summary(meeting.folder))
```

If `archive_material_mode == "transcript"`, render transcript read-only in a `QTextBrowser` or `QPlainTextEdit` with read-only state and show `Показать итог`.

- [ ] **Step 7: Save archive summary**

Add:

```python
def save_archive_summary(self, content: str) -> None:
    if self.archive_open_material is None:
        return
    kind, folder = self.archive_open_material
    if kind == "day_summary":
        self.storage.save_day_summary(folder, content)
        self.status_label.setText("Итог дня сохранен локально.")
    elif kind == "meeting_summary":
        self.storage.save_meeting_summary(folder, content)
        self.status_label.setText("Итог встречи сохранен локально.")
    self._render_archive_detail()
```

- [ ] **Step 8: Run Archive tests**

Run:

```powershell
python -m pytest tests/test_ui.py::test_archive_day_card_is_clickable_without_open_button tests/test_ui.py::test_archive_summary_card_opens_preview_and_saves_single_file -q
```

Expected: tests pass.

- [ ] **Step 9: Commit**

```powershell
git add app/ui/main_window.py tests/test_ui.py
git commit -m "Перевести Архив на раскрытие итогов"
```

## Task 6: Поиск Архива по новой файловой модели

**Files:**
- Modify: `app/services/archive.py`
- Modify: `tests/test_archive.py`
- Modify: `tests/test_ui.py`

- [ ] **Step 1: Write failing archive search test**

Add or update:

```python
def test_search_archive_finds_single_summary_files(tmp_path) -> None:
    storage = StorageService(tmp_path, NoopRecorder())
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    storage.save_day_summary(day_folder, "Дневной релиз найден")
    meeting = storage.create_meeting_folder("План", datetime(2026, 6, 12, 10, 0))
    storage.save_meeting_summary(meeting, "Встреча про релиз найдена")

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))
    matches = search_archive(days, "релиз")

    assert {match.kind for match in matches} >= {"Итоги дня", "Итоги встречи"}
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest tests/test_archive.py::test_search_archive_finds_single_summary_files -q
```

Expected: fails if search still only reads legacy draft/final files.

- [ ] **Step 3: Update archive search sources**

In `app/services/archive.py`, replace direct legacy file lists with new primary files plus fallback:

```python
day_summary_paths = [
    archive_day.folder / "00_day_summary.md",
    archive_day.folder / "00_day_summary_draft.md",
    archive_day.folder / "00_day_summary_final.md",
]
meeting_summary_paths = [
    meeting.folder / "summary.md",
    meeting.folder / "summary_draft.md",
    meeting.folder / "summary_final.md",
]
```

When multiple files contain the same query, prefer the first existing primary match so the UI does not duplicate results for one material.

- [ ] **Step 4: Run archive tests**

Run:

```powershell
python -m pytest tests/test_archive.py tests/test_ui.py::test_archive_search_match_opens_matched_transcript tests/test_ui.py::test_archive_search_detail_shows_only_relevant_meetings -q
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/services/archive.py tests/test_archive.py tests/test_ui.py
git commit -m "Обновить поиск Архива по итогам"
```

## Task 7: Удалить пользовательские следы draft/final

**Files:**
- Modify: `app/ui/main_window.py`
- Modify: `app/services/readiness.py`
- Modify: `app/services/summarization.py`
- Modify: `app/config.py`
- Modify: `tests/test_ui.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_summarization.py`

- [ ] **Step 1: Add text regression test**

Add a UI text test:

```python
def test_summary_ui_no_longer_shows_draft_or_final_words(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date.today())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder("Проверка", datetime.fromisoformat(f"{day_folder.name}T10:00:00"))
    storage.save_meeting_summary(meeting, "# Итог\n")
    storage.save_day_summary(day_folder, "# Итог дня\n")

    window = MainWindow(storage, recorder)
    window.open_review()
    window.open_archive()

    visible_text = "\n".join(
        [label.text() for label in window.findChildren(QLabel)]
        + [button.text() for button in window.findChildren(QPushButton)]
    ).casefold()

    assert "чернов" not in visible_text
    assert "финал" not in visible_text
    assert "draft" not in visible_text
    assert "final" not in visible_text
```

This test covers visible UI only. It does not ban legacy filenames in code paths.

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest tests/test_ui.py::test_summary_ui_no_longer_shows_draft_or_final_words -q
```

Expected: fails because old buttons or messages still contain old terms.

- [ ] **Step 3: Replace user-facing text**

Replace visible strings:

```python
"черновик"
"черновики"
"финальные файлы"
"финал"
"summary_draft.md готов к ревью."
"00_day_summary_draft.md готов."
```

with:

```python
"итог"
"итоги"
"итоговые файлы"
"summary.md готов к ревью."
"00_day_summary.md готов."
```

Do not rename internal compatibility methods if they are still needed for fallback.

- [ ] **Step 4: Run text scan**

Run:

```powershell
rg -n "Сохранить чернов|Сохранить финал|Открыть папку дня|summary_draft.md готов|00_day_summary_draft.md готов|Просмотреть transcript" app tests
```

Expected: no matches for visible UI strings. Matches in legacy storage tests are acceptable only if the test name or assertion explicitly covers fallback behavior.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_ui.py tests/test_storage.py tests/test_summarization.py tests/test_archive.py -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```powershell
git add app tests
git commit -m "Убрать старую терминологию итогов из UI"
```

## Task 8: Full verification and PROJECT_STATE

**Files:**
- Modify: `PROJECT_STATE.md`

- [ ] **Step 1: Run full tests**

Run:

```powershell
python -m pytest
python -m compileall -q app
```

Expected:

- pytest passes;
- compileall exits with code 0.

- [ ] **Step 2: Manual UI smoke**

Run the app locally and check:

- `Ревью` opens a meeting summary as rendered preview;
- `Редактировать` switches to editor;
- `Сохранить` updates `summary.md`;
- `Отмена` discards unsaved edits;
- `Транскрипт` remains read-only;
- old Review buttons are absent;
- `Архив` day card opens by clicking the card;
- only one Archive material card is open;
- Archive card actions are in header;
- Archive save writes `summary.md` or `00_day_summary.md`;
- `Повторить обработку` shows the existing warning;
- `Обновить итоги дня` shows the existing warning.

- [ ] **Step 3: Update PROJECT_STATE.md**

Add a changelog entry:

```markdown
- Ветка `codex/rendered-summary-single-file`: этап 8, новая модель просмотра и редактирования итогов. `Ревью` и `Архив` показывают красивый просмотр Markdown по умолчанию, редактирование включается кнопкой `Редактировать`, сохранение идет одной кнопкой `Сохранить` в `summary.md` или `00_day_summary.md`. Старые кнопки `Сохранить черновики`, `Сохранить финальные файлы`, `Открыть папку дня`, `Сохранить черновик`, `Сохранить финал` удалены из UI. Повторная обработка встречи и обновление итогов дня сохраняют предупреждения о перезаписи ручных изменений. Legacy-файлы draft/final не удаляются и читаются только как fallback. Этап 8 остается `На проверке`, этап 9 не начинался. Проверки: `python -m pytest` — записать фактический результат после запуска; `python -m compileall -q app` — записать фактический результат после запуска.
```

- [ ] **Step 4: Commit**

```powershell
git add PROJECT_STATE.md
git commit -m "Обновить состояние проекта по итогам"
```

## Final PR checklist

- [ ] `git status --short` shows only intentional changes before final commit.
- [ ] `rg -n "Сохранить чернов|Сохранить финал|Открыть папку дня|Просмотреть transcript" app tests` has no active UI matches.
- [ ] `rg -n "summary_draft.md|summary_final.md|00_day_summary_draft.md|00_day_summary_final.md" app tests` shows only compatibility/fallback references and updated tests.
- [ ] `python -m pytest` passes.
- [ ] `python -m compileall -q app` succeeds.
- [ ] PR title is Russian.
- [ ] PR body lists tests and states that stage 8 remains `На проверке`.

## Self-review

- Spec coverage: storage, generation, Review UI, Archive UI, search, warning behavior and removed buttons are covered by Tasks 1-8.
- Placeholder scan: the plan contains no open product decisions or PR-number templates.
- Type consistency: planned methods are consistently named `read_meeting_summary`, `save_meeting_summary`, `read_day_summary`, `save_day_summary`, `SummaryMaterialView`, `review_summary_view`, `archive_summary_view`.
