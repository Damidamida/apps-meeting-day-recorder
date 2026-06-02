@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    call :echo_utf8 "0JvQvtC60LDQu9GM0L3QvtC1IFB5dGhvbi3QvtC60YDRg9C20LXQvdC40LUg0L3QtSDQvdCw0LnQtNC10L3Qvi4="
    call :echo_utf8 "0KHQvdCw0YfQsNC70LAg0LLRi9C/0L7Qu9C90LjRgtC1INGD0YHRgtCw0L3QvtCy0LrRgyDQuNC3IFJFQURNRTo="
    echo python -m venv .venv
    echo .\.venv\Scripts\Activate.ps1
    echo python -m pip install --upgrade pip
    echo pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

"%PYTHON%" -m app.main
if errorlevel 1 (
    echo.
    call :echo_utf8 "0J3QtSDRg9C00LDQu9C+0YHRjCDQt9Cw0L/Rg9GB0YLQuNGC0Ywg0L/RgNC40LvQvtC20LXQvdC40LUu"
    call :echo_utf8 "0J/RgNC+0LLQtdGA0YzRgtC1INGB0L7QvtCx0YnQtdC90LjQtSDQvtCxINC+0YjQuNCx0LrQtSDQstGL0YjQtS4="
    echo.
    pause
    exit /b 1
)

exit /b 0

:echo_utf8
powershell.exe -NoProfile -Command "[Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::WriteLine([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%~1')))"
exit /b 0
