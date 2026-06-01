# Meeting Day Recorder

A local-first Windows desktop app skeleton for manually tracking ad hoc workday meetings. This first version creates a safe foundation without recording or AI integrations.

## Windows setup

Install Python 3.11 or newer. From PowerShell in the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item config.yaml.example config.yaml
```

## Run the app

```powershell
python -m app.main
```

## Run tests

```powershell
pytest
```

## MVP skeleton

Implemented:

- PySide6 desktop window with manual workday, meeting, review, and summary controls.
- Local placeholder flow: start workday, start meeting, end meeting, and end workday.
- Button states that prevent invalid workday and meeting actions.
- Local storage helpers for dated workday folders and safe meeting folder names.
- JSON day and meeting metadata, including meeting duration.
- Placeholder transcript, draft meeting summary, day summary, and task files.
- YAML configuration loading with sensible defaults.

Intentionally not implemented yet:

- OBS recording integration.
- ffmpeg audio extraction.
- Real transcription or diarization.
- Real OpenAI API calls or AI summarization.
- OCR.
- Email, calendar, or messenger integrations.

