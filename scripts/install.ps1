# Установка «Антимошенника» на Windows (PowerShell). Нужен интернет; после установки всё работает без сети.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1           # qwen3:8b + bge-m3 + Whisper small
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Lite     # qwen3:4b (8 ГБ RAM)
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -All      # обе LLM, Whisper small + medium
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -NoModels
param([switch]$Lite, [switch]$All, [switch]$NoModels)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ОШИБКА: $msg" -ForegroundColor Red; exit 1 }
function Check() { if ($LASTEXITCODE -ne 0) { Fail "команда завершилась с кодом $LASTEXITCODE" } }

$LlmModels = @("qwen3:8b")
$WhisperModels = @($(if ($env:ANTIFRAUD_WHISPER_MODEL) { $env:ANTIFRAUD_WHISPER_MODEL } else { "small" }))
if ($Lite) { $LlmModels = @("qwen3:4b") }
if ($All) { $LlmModels = @("qwen3:8b", "qwen3:4b"); $WhisperModels = @("small", "medium") }
$EmbedModel = "bge-m3"

Step "Python 3.11"
$py = $null
try { & py -3.11 -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-3.11") } } catch {}
if (-not $py) {
    try {
        $v = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -eq "3.11") { $py = @("python") }
    } catch {}
}
if (-not $py) { Fail "нужен Python 3.11: winget install Python.Python.3.11" }
Write-Host "найден $($py -join ' ')"

Step "Виртуальное окружение .venv и зависимости"
if (-not (Test-Path .venv)) { & $py[0] $py[1..9] -m venv .venv; Check }
$vpy = ".venv\Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip; Check
& $vpy -m pip install --quiet -r requirements.txt; Check
& $vpy -m pip check; Check

Step "Демо-БД (20 вымышленных номеров)"
& $vpy -m storage.seed_demo; Check

Step "Проверка базы схем"
& $vpy data\schemes\validate.py | Select-Object -Last 2; Check

if ($NoModels) { Write-Host "`nГотово (без моделей). Запуск: scripts\run.ps1"; exit 0 }

Step "Ollama"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements; Check
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    } else { Fail "поставьте Ollama вручную: https://ollama.com/download" }
}
ollama --version

function OllamaUp { try { Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 2 | Out-Null; $true } catch { $false } }
if (-not (OllamaUp)) {
    Write-Host "запускаю ollama serve в фоне"
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (OllamaUp); $i++) { Start-Sleep 1 }
    if (-not (OllamaUp)) { Fail "Ollama не отвечает" }
}

Step "Модели Ollama: $($LlmModels -join ' ') $EmbedModel"
foreach ($m in $LlmModels + $EmbedModel) { ollama pull $m; Check }

Step "Модели Whisper: $($WhisperModels -join ' ')"
foreach ($m in $WhisperModels) {
    & $vpy -c "from faster_whisper import WhisperModel; WhisperModel('$m', device='cpu', compute_type='int8'); print('$m — ок')"; Check
}
& $vpy -c "from silero_vad import load_silero_vad; load_silero_vad(onnx=True); print('silero-vad — ок')"; Check

Step "Проверка распознавания на data\calls\011.wav"
$env:HF_HUB_OFFLINE = "1"
& $vpy -m audio.listen --file data\calls\011.wav --model $WhisperModels[0] | Select-Object -First 2

Write-Host "`nГотово. Запуск: scripts\run.ps1"
