# Bundled FFmpeg

`ffmpeg.exe` не хранится в репозитории. Для сборки установщика положите Windows-бинарь в:

```text
packaging/ffmpeg/bin/ffmpeg.exe
```

Альтернативно передайте путь через переменную окружения `BK_SCRIBE_FFMPEG`; скрипт `scripts/build_windows_package.ps1` скопирует файл в нужное место перед сборкой.

После сборки PyInstaller включает файл как:

```text
resources/ffmpeg/ffmpeg.exe
```

Приложение ищет этот bundled FFmpeg до системного `PATH`, поэтому установленный BK Scribe может извлекать аудио без отдельной настройки FFmpeg в Windows.
