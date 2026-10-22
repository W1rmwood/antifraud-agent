# Запуск интерфейса «Антимошенника» одной командой (Windows). Сеть не нужна.
# Агент и MCP-сервер стартуют из интерфейса по кнопке «Старт».
#
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1         # проверяет qwen3:8b
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Lite   # проверяет qwen3:4b
param([switch]$Lite)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Fail($msg) { Write-Host "ОШИБКА: $msg" -ForegroundColor Red; exit 1 }

$LlmModel = $(if ($env:ANTIFRAUD_LLM) { $env:ANTIFRAUD_LLM } else { "qwen3:8b" })
if ($Lite) { $LlmModel = "qwen3:4b" }
$EmbedModel = "bge-m3"
$env:ANTIFRAUD_LLM = $LlmModel
# Модели берутся только из локального кэша: никаких запросов к Hugging Face во время звонка.
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"

$vpy = ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Fail "нет .venv — сначала scripts\install.ps1" }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Fail "Ollama не установлена — scripts\install.ps1" }

function Tags { try { Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 2 } catch { $null } }
if (-not (Tags)) {
    Write-Host "Ollama не запущена, запускаю ollama serve в фоне"
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Tags); $i++) { Start-Sleep 1 }
    if (-not (Tags)) { Fail "Ollama не отвечает" }
}
$names = (Tags).models | ForEach-Object { $_.name }
foreach ($m in @($LlmModel, $EmbedModel)) {
    if (-not ($names | Where-Object { $_ -like "$m*" })) { Fail "модели $m нет в Ollama — ollama pull $m (нужен интернет)" }
}
Write-Host "Ollama: $LlmModel и $EmbedModel на месте"

if (-not (Test-Path data\antifraud.db)) { & $vpy -m storage.seed_demo }

& $vpy -m streamlit run ui\app.py
