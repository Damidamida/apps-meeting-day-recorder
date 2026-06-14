# Восстановление прошлых рабочих дней Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать ручное восстановление прошлого активного рабочего дня без смешивания встреч разных дат.

**Architecture:** `StorageService` отвечает за поиск прошлых активных дней, завершение конкретной папки дня и восстановление interrupted meeting pipeline. `MainWindow` показывает отдельную карточку на вкладке `Рабочий день`, ставит встречи прошлого дня в существующую последовательную очередь и запускает дневные итоги для этой же папки после обработки встреч.

**Tech Stack:** Python 3.11, PySide6, pytest, локальное JSON-хранилище.

---

### Task 1: Storage behavior

**Files:**
- Modify: `app/services/storage.py`
- Test: `tests/test_storage.py`

- [x] Add failing tests for finding the latest past active workday and ending a specific day folder without touching today's active day.
- [x] Run the new storage tests and verify they fail because the methods do not exist yet.
- [x] Add focused `StorageService` methods for `find_past_active_workday`, `end_workday_folder`, and pending processing discovery for an arbitrary day.
- [x] Run the storage tests and verify they pass.

### Task 2: Workday screen recovery card

**Files:**
- Modify: `app/ui/main_window.py`
- Test: `tests/test_ui.py`

- [x] Add failing UI tests for showing the card, keeping past meetings out of today's list, allowing today's workday start, and clicking `Завершить день и сформировать итоги`.
- [x] Run the new UI tests and verify they fail because the card and recovery flow are absent.
- [x] Add `MainWindow` state and rendering for the recovery card with Russian labels from the spec.
- [x] Wire the card button to end the past day, enqueue pending/running meetings from that day, and request day summary for that same day.
- [x] Reuse existing pipeline sequencing so today's queue and past recovery queue do not run heavy work in parallel.
- [x] Run the UI tests and verify they pass.

### Task 3: Project state and verification

**Files:**
- Modify: `PROJECT_STATE.md`

- [x] Update project state with status, implemented work, next step, risks/decisions, changelog entry for `codex/recover-past-workdays`, and test results.
- [x] Run `python -m pytest`.
- [x] Run `python -m compileall -q app`.
- [x] Commit changes and create a PR to `main` with a Russian title and description.
