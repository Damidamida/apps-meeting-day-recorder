param(
    [string]$InnoSetupPath = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$specPath = Join-Path $repoRoot "packaging\pyinstaller\bk_scribe.spec"
$ffmpegDestination = Join-Path $repoRoot "packaging\ffmpeg\bin\ffmpeg.exe"
$ffmpegSource = $env:BK_SCRIBE_FFMPEG

Set-Location $repoRoot

if ($ffmpegSource -and (Test-Path -LiteralPath $ffmpegSource)) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $ffmpegDestination) | Out-Null
    Copy-Item -LiteralPath $ffmpegSource -Destination $ffmpegDestination -Force
    Write-Host "FFmpeg добавлен в сборку: packaging\ffmpeg\bin\ffmpeg.exe"
} elseif (-not (Test-Path -LiteralPath $ffmpegDestination)) {
    Write-Warning "Bundled FFmpeg не найден. Установщик соберется без resources\ffmpeg\ffmpeg.exe."
    Write-Warning "Для полной сборки положите файл в packaging\ffmpeg\bin\ffmpeg.exe или задайте BK_SCRIBE_FFMPEG."
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

& $python -m pip install -e ".[dev]"
& $python -m PyInstaller --clean --noconfirm $specPath

if ($SkipInstaller) {
    Write-Host "Сборка приложения готова: dist\BK Scribe"
    exit 0
}

if (-not $InnoSetupPath) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            $InnoSetupPath = $candidate
            break
        }
    }
}

if (-not $InnoSetupPath -or -not (Test-Path -LiteralPath $InnoSetupPath)) {
    Write-Warning "ISCC.exe не найден. Приложение собрано, установщик Inno Setup пропущен."
    Write-Warning "Установите Inno Setup 6 или передайте -InnoSetupPath."
    exit 0
}

$innoScript = Join-Path $repoRoot "packaging\inno\bk_scribe.iss"
& $InnoSetupPath $innoScript
Write-Host "Установщик готов: packaging\output\BK-Scribe-Setup.exe"
