# Мастер первого запуска BK Scribe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать обязательный мастер первого запуска BK Scribe как полноэкранный setup-gate внутри приложения.

**Architecture:** Мастер добавляется как отдельная страница основного `QStackedWidget` и использует существующие сервисы конфигурации, readiness, OBS, FFmpeg, транскрипции и summary. Настройка идет строго последовательно: следующий шаг доступен только после успешной проверки предыдущего, а `Рабочий день`, `Ревью`, `Архив` и floating control заблокированы до завершения мастера.

Автопоказ мастера определяется только локальной секцией `setup` в `config.yaml`: мастер открывается при отсутствии или повреждении `config.yaml`, отсутствии `setup`, `setup.completed != true` или `setup.version < CURRENT_SETUP_VERSION`. Обычные readiness-ошибки после уже пройденного мастера (`OBS не подключен`, ключ удален из `.env`, AI Tunnel временно недоступен) не сбрасывают `setup.completed` и не возвращают приложение в режим первого запуска; пользователь видит понятный статус и может открыть мастер вручную из `Настройки`.

**Tech Stack:** Python 3.11+, PySide6, pytest, существующие `load_config`, `StorageService`, `check_readiness`, `load_api_key`, `create_transcriber`, `create_summarizer`, `OpenAISummarizer`.

---

## Контекст

Репозиторий: `Damidamida/apps-meeting-day-recorder`.

Базовая ветка: свежий `main` после merge PR #64 и follow-up PR #65 по Windows-установщику и bundled FFmpeg.

Рабочая ветка реализации: `codex/first-run-wizard`.

Текущий этап: Stage 10 `Windows packaging` уже `На проверке` по PR 1, но не `Готово`. PR 2 не принимает этап, а переводит результат мастера на проверку через отдельный PR.

Перед реализацией обязательно прочитать:

- `AGENTS.md`;
- `PROJECT_STATE.md`;
- `docs/superpowers/specs/2026-06-14-windows-packaging-and-first-run-design.md`;
- `docs/mockups/first-run-wizard-bk-scribe.html`;
- этот план.

## Scope PR 2

Входит:

- полноэкранная страница `Настройка BK Scribe` внутри основного окна;
- строгая последовательность шагов `Папка данных` → `OBS` → `Аудио` → `AI Tunnel` → `Транскрипция` → `AI-итоги` → `Начать работу`;
- блокировка `Рабочий день`, `Ревью`, `Архив`, старта рабочего дня и floating control до успешной настройки;
- повторный запуск мастера из `Настройки`;
- сохранение настроек только после успешных проверок;
- сохранение AI Tunnel key в локальный `.env` / `secrets.env_file` только после успешной проверки;
- хранение `setup.version` и признаков успешных проверок для будущей донастройки;
- русские тексты и статусы без stack trace.

Не входит:

- изменение установщика и Inno Setup;
- автоматическая установка OBS;
- автоматическая настройка OBS;
- миграция старых рабочих дней;
- изменение структуры папок рабочих дней;
- реальные встречи, записи, transcript или summary во время проверок мастера;
- коммит `config.yaml`, `.env`, аудио, transcript, summary или секретов.

## Файлы

Создать:

- `app/services/first_run.py` — модель состояния мастера, последовательность шагов, проверки папки данных, AI Tunnel key, транскрипции и AI-итогов.
- `app/ui/first_run_wizard.py` — полноэкранная PySide6-страница мастера.
- `tests/test_first_run.py` — unit-тесты состояния, сброса зависимых шагов и сохранения.
- `tests/test_first_run_ui.py` — UI-тесты wizard-страницы и setup-gate.

Изменить:

- `app/config.py` — default-секция `setup`, нормализация статуса мастера, helper сохранения конфигурации.
- `app/services/readiness.py` — публичные функции проверки FFmpeg/OBS/AI без привязки к карточкам главного экрана, если текущие private helpers неудобны.
- `app/services/summarization.py` — дешевый smoke-test AI-итогов на коротком синтетическом тексте.
- `app/ui/main_window.py` — подключение wizard-страницы, блокировка навигации и start-workday gate, кнопка мастера в настройках.
- `README.md` — короткое описание первого запуска для коллег.
- `PROJECT_STATE.md` — запись о PR 2, тестах и статусе Stage 10.

## UX и сохранение

Визуальный референс: `docs/mockups/first-run-wizard-bk-scribe.html`.

Правила сохранения:

- ввод в поля сам по себе ничего не пишет на диск;
- `Папка данных` сохраняется после успешной write-check;
- OBS считается сохраненным/подтвержденным после успешного WebSocket-check;
- `Аудио` сохраняет статус готовности после найденного bundled FFmpeg;
- `AI Tunnel key` записывается в локальный `.env` только после успешной проверки ключа;
- `Транскрипция` сохраняет выбранный backend/model только после успешной проверки транскрипции;
- в мастере `Транскрипция` использует только фиксированные dropdown-списки моделей: для `AI Tunnel STT` доступны `Whisper Large V3 Turbo`, `Whisper Large V3`, `Whisper 1`, для локальных backend — готовые списки Whisper-моделей без ручного custom-ввода;
- `AI-итоги` сохраняют `summary.enabled=true`, модель и endpoint только после успешной проверки summary;
- `Далее` не сохраняет неподтвержденные значения, а только переводит к следующему шагу, когда текущий шаг `Готово`.
- после успешного завершения сохраняются `setup.completed: true`, `setup.version: 1`, `completed_at` и флаги `data_root_checked`, `obs_checked`, `audio_checked`, `aitunnel_checked`, `transcription_checked`, `summary_checked`;
- readiness-проверки обычного запуска не меняют `setup.completed` обратно на `false`.

## Tasks

### Task 1: Config model and first-run state

**Files:**
- Modify: `app/config.py`
- Create: `app/services/first_run.py`
- Test: `tests/test_first_run.py`, `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

- `setup.completed` defaults to `False`;
- step order is exactly `data_root`, `obs`, `audio`, `aitunnel`, `transcription`, `summary`, `finish`;
- a future step cannot be active before previous step is `ok`;
- changing `AITUNNEL_KEY` resets `aitunnel`, `transcription`, `summary`, `finish`;
- `setup_completed(state)` is true only when all required checks are `ok`.

Run:

```powershell
python -m pytest tests/test_first_run.py tests/test_config.py -q
```

Expected before implementation: tests fail because first-run state does not exist.

- [ ] **Step 2: Implement state model**

Create `app/services/first_run.py` with these public names:

```python
FIRST_RUN_STEPS = ("data_root", "obs", "audio", "aitunnel", "transcription", "summary", "finish")

@dataclass(frozen=True)
class FirstRunStepState:
    key: str
    title: str
    status: str
    message: str

@dataclass
class FirstRunState:
    completed: bool
    current_step: str
    steps: dict[str, FirstRunStepState]
```

Statuses: `locked`, `todo`, `checking`, `ok`, `error`.

Add helpers:

- `default_setup_config()`;
- `normalize_setup_config(value)`;
- `can_open_step(state, step_key)`;
- `mark_step_ok(state, step_key, message)`;
- `mark_step_error(state, step_key, message)`;
- `reset_from_step(state, step_key)`;
- `setup_completed(state)`.

- [ ] **Step 3: Wire config defaults**

In `app/config.py`, add a `setup` default section with `completed: False`, step statuses, timestamps and selected values. Normalize missing/malformed setup config safely.

Run:

```powershell
python -m pytest tests/test_first_run.py tests/test_config.py -q
```

Expected after implementation: selected tests pass.

### Task 2: Local persistence and AI Tunnel key handling

**Files:**
- Modify: `app/services/first_run.py`
- Test: `tests/test_first_run.py`

- [ ] **Step 1: Write failing tests**

Cover:

- data folder check creates folder and writes/removes probe file;
- selecting a file path returns error;
- AI Tunnel key is masked in returned status;
- successful AI Tunnel check writes `AITUNNEL_KEY=...` to the configured `.env`;
- failed AI Tunnel check does not write the key;
- existing unrelated `.env` lines are preserved.

Run:

```powershell
python -m pytest tests/test_first_run.py -q
```

- [ ] **Step 2: Implement checks**

Implement:

- `default_data_root() -> Path` as `Path.home() / "Documents" / "BK Scribe"`;
- `validate_data_root(path: Path)`;
- `resolve_first_run_env_file(config: dict)`;
- `write_env_secret(env_file: Path, name: str, value: str)`;
- `check_aitunnel_key(key: str, config: dict, client_factory=None)`.

The AI check must use a cheap request and return only simple Russian messages:

- `Ключ AI Tunnel проверен.`
- `Ключ не подошел.`
- `Сервис временно недоступен.`
- `Введите AI Tunnel key.`

Never include the raw key in exception text, logs, metadata or status messages.

Run:

```powershell
python -m pytest tests/test_first_run.py -q
```

Expected: tests pass.

### Task 3: Transcription and AI summary smoke checks

**Files:**
- Modify: `app/services/first_run.py`
- Modify: `app/services/summarization.py`
- Test: `tests/test_first_run.py`, `tests/test_summarization.py`

- [ ] **Step 1: Write failing tests**

Cover:

- default transcription option is `aitunnel`;
- dropdown options are ordered: `AI Tunnel STT`, `faster-whisper`, `Whisper CLI`;
- AI Tunnel transcription check returns `Сначала проверьте ключ AI Tunnel.` when key step is not ok;
- summary check returns `Сначала проверьте ключ AI Tunnel.` when key step is not ok;
- summary smoke test sends a short synthetic text and never reads real transcript/summary files.

Run:

```powershell
python -m pytest tests/test_first_run.py tests/test_summarization.py -q
```

- [ ] **Step 2: Implement smoke checks**

Add:

- `TRANSCRIPTION_OPTIONS`;
- `SUMMARY_MODEL_OPTIONS` reuse or mirror current UI values from `app/ui/main_window.py`;
- `check_transcription_settings(config, setup_state)`;
- `smoke_test_summary_connection(config, client_factory=None)`;
- `check_summary_settings(config, setup_state)`.

For local `faster-whisper` and `Whisper CLI`, checks may reuse current readiness behavior. For `AI Tunnel STT`, the check depends on the already verified key and chosen model. It must not upload real audio during the first-run wizard.

Run:

```powershell
python -m pytest tests/test_first_run.py tests/test_summarization.py -q
```

Expected: tests pass.

### Task 4: Full-screen wizard UI

**Files:**
- Create: `app/ui/first_run_wizard.py`
- Modify: `app/ui/main_window.py`
- Test: `tests/test_first_run_ui.py`

- [ ] **Step 1: Write failing UI tests**

Cover:

- wizard is a page/widget inside the main app, not a modal popup chain;
- top card `Готовность к работе` is absent;
- left and right columns have matching fixed/minimum height behavior;
- only the first incomplete step is enabled;
- `Далее` is disabled until the current step is `Готово`;
- AI Tunnel step contains link `https://aitunnel.ru/`;
- transcription step uses dropdown with `AI Tunnel STT` first and selected by default;
- summary step has no key input.

Run:

```powershell
python -m pytest tests/test_first_run_ui.py -q
```

- [ ] **Step 2: Build wizard page**

Use the visual reference `docs/mockups/first-run-wizard-bk-scribe.html`.

Required UI:

- title `Настройка BK Scribe`;
- compact progress text;
- left step list with statuses `Готово`, `Требует действия`, `Заблокировано`;
- right step content;
- buttons `Назад`, `Далее`, `Начать работу`;
- no top `Готовность к работе` panel;
- equal-height left/right layout;
- Russian-only user text.

Run:

```powershell
python -m pytest tests/test_first_run_ui.py -q
```

Expected: tests pass.

### Task 5: Setup-gate in MainWindow

**Files:**
- Modify: `app/ui/main_window.py`
- Test: `tests/test_first_run_ui.py`, `tests/test_ui.py`

- [ ] **Step 1: Write failing gate tests**

Cover:

- first launch with `setup.completed=false` opens the wizard page;
- `Рабочий день`, `Ревью`, `Архив` cannot be opened until setup is complete;
- `Настройки` and `Справка` remain available;
- `start_workday()` refuses to start and shows a Russian message until setup complete;
- floating control start-workday request is ignored until setup complete;
- after completion, navigation and start workday work normally.

Run:

```powershell
python -m pytest tests/test_first_run_ui.py tests/test_ui.py -q
```

- [ ] **Step 2: Implement gate**

In `MainWindow`:

- add setup page to `self.pages`;
- redirect startup to setup page if needed;
- disable or guard navigation buttons for gated sections;
- guard `start_workday` and floating start action;
- add settings action `Открыть мастер настройки`;
- when setup completes, save config, rebuild `StorageService` root if safe, refresh UI, and return to `Рабочий день`.

Run:

```powershell
python -m pytest tests/test_first_run_ui.py tests/test_ui.py -q
```

Expected: tests pass.

### Task 6: Docs, state and verification

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_STATE.md`

- [ ] **Step 1: Update docs**

README must explain:

- first launch opens `Настройка BK Scribe`;
- default data folder is `Документы\BK Scribe`;
- OBS must be installed and WebSocket enabled by the user;
- bundled FFmpeg is checked automatically;
- one AI Tunnel key is used for transcription and summaries;
- AI Tunnel key can be obtained at `https://aitunnel.ru/`;
- wizard checks do not create real meetings, recordings, transcript or summary.

PROJECT_STATE must mention:

- branch `codex/first-run-wizard`;
- PR number if already known;
- exact checks run;
- Stage 10 remains `На проверке`, not `Готово`.

- [ ] **Step 2: Run final verification**

Run:

```powershell
python -m pytest
python -m compileall -q app
git status --short
```

Known baseline note: if old unrelated review UI failures reproduce from `main`, document exact failing tests in PR body and `PROJECT_STATE.md`.

- [ ] **Step 3: Create PR**

Create PR with Russian title:

```text
Добавить мастер первого запуска BK Scribe
```

PR body must include:

- что сделано;
- что не входило в scope;
- как проверено;
- что Stage 10 не считается `Готово` без приемки пользователя.

## Acceptance

- При первом запуске открывается `Настройка BK Scribe`.
- Без успешного мастера нельзя начать рабочий день.
- Нельзя перейти к следующему шагу без `Готово` на текущем.
- `AI Tunnel key` вводится один раз и используется для транскрипции и итогов.
- При выборе `AI Tunnel STT` есть ссылка на `https://aitunnel.ru/`.
- Транскрипция по умолчанию — `AI Tunnel STT`.
- В мастере нельзя ввести произвольную custom-модель транскрипции; модели выбираются только из фиксированных списков.
- AI-итоги не дублируют ввод ключа.
- Ключ пишется в локальный `.env` только после успешной проверки.
- Проверки мастера не создают реальные встречи, записи, transcript или summary.
- Все тексты мастера на русском.
- `config.yaml`, `.env`, записи, audio, transcript, summary и секреты не попадают в git.
