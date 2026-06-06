# Meeting Day Recorder

Локальное Windows-приложение для ручного учета ad hoc-встреч в течение рабочего дня. Текущая версия сохраняет local-first сценарий записи и извлечения аудио, а транскрипцию можно выполнять локально или явно переключить на внешний AI Tunnel STT backend.

Актуальное состояние проекта, ограничения и план следующих этапов описаны в [`PROJECT_STATE.md`](PROJECT_STATE.md). Этот файл является единым источником истины для текущего контекста проекта.

## Установка в Windows

Установите Python 3.11 или новее. В PowerShell из папки репозитория выполните:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item config.yaml.example config.yaml
```

## Запуск приложения

```powershell
python -m app.main
```

## Запуск двойным кликом в Windows

После установки зависимостей приложение можно запускать без ручного PowerShell через файл:

`start_meeting_day_recorder.cmd`

Файл использует локальное окружение `.venv` и запускает:

`.venv\Scripts\python.exe -m app.main`

Если `.venv` еще не создано, выполните установку из раздела "Установка в Windows". Консольное окно остается видимым при ошибке, чтобы можно было прочитать сообщение.

## Запуск тестов

```powershell
pytest
```

## Реализовано

- Окно PySide6 с ручным управлением рабочим днем и встречами.
- Локальный сценарий: начать рабочий день, начать встречу, завершить встречу, завершить рабочий день.
- Восстановление активного рабочего дня и активной встречи после перезапуска.
- Блокировка недопустимых действий через состояние кнопок.
- Русская навигационная оболочка со страницами рабочего дня, ревью и справки.
- Локальное ревью черновиков итогов встречи, итогов дня и задач.
- Сохранение финальных Markdown-файлов без удаления черновиков.
- Безопасная интеграция с OBS для запуска и остановки записи встречи.
- Индикатор состояния OBS и кнопка ручной проверки подключения.
- Кнопка `Проверить готовность` со статусами OBS, FFmpeg, Whisper, summary, API key, endpoint и папки данных.
- Визуальная статусная модель pipeline встречи: запись, извлечение аудио, транскрипция, генерация итогов и финальный статус.
- Локальное извлечение `audio.wav` из OBS-записи через FFmpeg.
- Транскрипция `audio.wav` через optional Whisper CLI, optional faster-whisper или явно включаемый внешний backend AI Tunnel.
- Генерация `summary_draft.md` одной встречи через AI Tunnel / OpenAI-compatible endpoint из готового текстового транскрипта, если summary явно включен в локальном `config.yaml`.
- Тяжелые шаги обработки встречи выполняются в фоне, чтобы UI не зависал.
- После остановки записи можно сразу начать следующую встречу; FFmpeg, Whisper и summary по предыдущей встрече продолжают выполняться в очереди.
- Локальные папки по датам и безопасные имена папок встреч.
- JSON-метаданные рабочего дня и встреч, включая длительность встречи.
- Markdown- и JSON-заглушки транскриптов, черновиков итогов дня и задач.
- Загрузка YAML-конфигурации со значениями по умолчанию.

## Интеграция с OBS

Интеграция с OBS добавлена на этапе 4 и по умолчанию выключена. Без OBS приложение продолжает работать в локальном режиме с существующими placeholder-файлами.

Для включения записи установите и запустите OBS Studio. В OBS откройте настройки WebSocket через меню `Инструменты`, включите сервер WebSocket и задайте пароль. Затем укажите локальные настройки в `config.yaml`:

```yaml
obs:
  enabled: true
  websocket_host: "localhost"
  websocket_port: 4455
  websocket_password: "ваш локальный пароль"
```

Файл `config.yaml.example` остается безопасным шаблоном в репозитории. Для локального запуска создайте рядом с ним `config.yaml`, включите OBS и укажите пароль. Рабочий `config.yaml` не заменяет шаблон и не добавляется в git. Путь записи настраивается только в OBS; приложение не меняет каталог записи и сохраняет путь в metadata встречи только если OBS возвращает его после остановки записи.

## FFmpeg

На этапе 5 приложение использует FFmpeg для локального извлечения аудио из видеофайла OBS. FFmpeg должен быть установлен в Windows и доступен в `PATH`.

Приложение не отправляет видео или аудио во внешние сервисы на этом этапе. Извлеченный файл `audio.wav` сохраняется локально в папке встречи.

## Транскрипция

На этапе 6 приложение может использовать локальный CLI `whisper` для подготовки `transcript.md` и `transcript.json` из файла `audio.wav`. После ручной проверки добавлен optional backend `faster-whisper`, чтобы ускорять локальную транскрипцию без отправки аудио во внешние сервисы.

По умолчанию используется backend `whisper_cli` и multilingual-модель `base`: это совместимый стартовый вариант. Модель должна быть доступна локальному Whisper CLI; при первом запуске Whisper может скачать ее на диск.

Если `whisper` не установлен или недоступен в `PATH`, приложение не падает: встреча завершается, placeholder-файлы остаются на месте, а в metadata фиксируется причина, почему транскрипция не выполнена.

Для ускоренного локального backend установите optional-зависимость:

```powershell
python -m pip install -e ".[faster-whisper]"
```

Затем в локальном `config.yaml` укажите:

```yaml
transcription:
  backend: "faster_whisper"
  model: "base"
  language: "ru"
  device: "cpu"
  compute_type: "int8"
```

Для возврата к старому CLI-варианту:

```yaml
transcription:
  backend: "whisper_cli"
  model: "base"
  whisper_command: "whisper"
```

### Внешняя транскрипция через AI Tunnel

Опционально можно переключить транскрипцию на AI Tunnel. В этом режиме приложение отправляет во внешний STT endpoint файл `audio.wav`, получает текст и сохраняет локальные `transcript.md` и `transcript.json`.

Видео во внешний сервис не отправляется. Summary по-прежнему получает только текст transcript, не аудио и не видео.

По документации AI Tunnel endpoint `POST /v1/audio/transcriptions` совместим с OpenAI SDK, принимает `multipart/form-data`, поддерживает `wav`, `mp3`, `flac`, `m4a`, `ogg`, `webm`, `aac`, `mp4`, `mpga` и имеет лимит 25 МБ на файл. Для длинных записей позже нужен отдельный chunking-режим; в текущем PR отправляется целый `audio.wav`.

Пример настройки:

```yaml
transcription:
  backend: "aitunnel"
  model: "whisper-large-v3-turbo"
  language: "ru"
  api_key_env: "AITUNNEL_KEY"
  base_url: "https://api.aitunnel.ru/v1/"
  env_file: ""
  timeout_seconds: 300
  max_upload_mb: 25
```

API key не хранится в репозитории. Рекомендуемый способ — переменная окружения `AITUNNEL_KEY` или внешний `.env.local`, путь к которому указан в `transcription.env_file`.

## Генерация итогов через AI Tunnel

Приложение может использовать AI Tunnel как OpenAI-compatible endpoint для подготовки `summary_draft.md` из локального текстового транскрипта встречи.

Видео и аудио во внешний AI endpoint не отправляются. Отправляется только текст из `transcript.md` / `transcript.json`.

По умолчанию генерация итогов выключена. Для включения настройте `config.yaml`:

```yaml
summary:
  enabled: true
  provider: "openai"
  model: "gpt-5.4-mini"
  api_key_env: "AITUNNEL_KEY"
  base_url: "https://api.aitunnel.ru/v1/"
  env_file: ""
  timeout_seconds: 120
  max_chars_per_chunk: 20000
```

API key не хранится в репозитории. Рекомендуемый способ — переменная окружения `AITUNNEL_KEY`.

Для локального использования можно указать путь к внешнему `.env.local` в `summary.env_file`, но сам `.env.local` нельзя добавлять в git.

Если нужно временно вернуться на ProxyAPI, укажите ключ ProxyAPI и базовый URL ProxyAPI в локальном `config.yaml`:

```yaml
summary:
  enabled: true
  provider: "openai"
  model: "gpt-5.4-mini"
  api_key_env: "PROXYAPI_KEY"
  base_url: "https://api.proxyapi.ru/openai/v1"
  env_file: ""
  timeout_seconds: 120
  max_chars_per_chunk: 20000
```

В обоих режимах приложение продолжает использовать официальный OpenAI SDK, но отправляет запросы в выбранный OpenAI-compatible endpoint. Видео и аудио по-прежнему не отправляются для генерации итогов.

## Ручная проверка полного сценария

1. Установите зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

2. Создайте локальный `config.yaml` из `config.yaml.example`.
3. Настройте OBS WebSocket и путь записи в самом OBS.
4. Проверьте, что FFmpeg доступен в `PATH`:

```powershell
ffmpeg -version
```

5. Проверьте, что выбранный backend транскрипции доступен. Для `whisper_cli`:

```powershell
whisper --help
```

Для `faster_whisper` установите optional-зависимость и нажмите в приложении `Проверить готовность`:

```powershell
python -m pip install -e ".[faster-whisper]"
```

Для `aitunnel` укажите `transcription.api_key_env`, `transcription.base_url` и при необходимости `transcription.env_file`, затем нажмите в приложении `Проверить готовность`.

6. Если нужна генерация итогов, настройте `summary` в `config.yaml` и храните ключ только во внешнем окружении или `.env.local`, который не добавляется в git.
7. Запустите приложение двойным кликом через `start_meeting_day_recorder.cmd`.
8. Нажмите `Проверить готовность`.
9. Начните рабочий день.
10. Начните короткую встречу.
11. Завершите встречу и убедитесь, что после остановки записи кнопка `Начать встречу` снова доступна.
12. При необходимости начните следующий созвон, пока предыдущая встреча обрабатывается в фоне.
13. Дождитесь завершения фонового pipeline.
14. Проверьте папку встречи в `MeetingSummaries/YYYY-MM-DD/HH-MM_title/`.

Ожидаемые файлы:

- `meeting_metadata.json`;
- `audio.wav`;
- `transcript.md`;
- `transcript.json`;
- `summary_draft.md`, если summary включен и transcript готов.

Успешные статусы в `meeting_metadata.json`:

- `audio_status: extracted`;
- `transcription_status: completed`;
- `summary_status: draft_created`.

Безопасные пропуски:

- `summary_status: disabled` — генерация итогов выключена;
- `transcription_status: whisper_unavailable` — Whisper CLI недоступен;
- `transcription_status: faster_whisper_unavailable` — faster-whisper не установлен;
- `transcription_status: aitunnel_unavailable` — API key для внешней транскрипции не найден;
- `transcription_status: file_too_large` — `audio.wav` больше лимита внешней транскрипции;
- `summary_status: openai_unavailable` — API key не найден;
- `summary_status: skipped` — transcript не готов или пустой.

Если transcript пустой, приложение не отправляет запрос во внешний AI endpoint.

## Намеренно не реализовано

- Диаризация.
- Chunking для внешней транскрипции длинных аудиофайлов.
- Внешняя транскрипция через другие STT-провайдеры, кроме AI Tunnel.
- OCR.
- Интеграции с почтой, календарями и мессенджерами.
