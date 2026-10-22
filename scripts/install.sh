#!/usr/bin/env bash
# Установка «Антимошенника» на macOS / Linux. Нужен интернет; после установки всё работает без сети.
#
#   scripts/install.sh            # qwen3:8b + bge-m3 + Whisper small
#   scripts/install.sh --lite     # qwen3:4b вместо 8b (машины с 8 ГБ RAM)
#   scripts/install.sh --all      # обе LLM и Whisper small + medium
#   scripts/install.sh --no-models  # только Python-окружение и демо-БД
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

LLM_MODELS=("qwen3:8b")
WHISPER_MODELS=("${ANTIFRAUD_WHISPER_MODEL:-small}")
PULL_MODELS=1
for arg in "$@"; do
  case "$arg" in
    --lite) LLM_MODELS=("qwen3:4b") ;;
    --all) LLM_MODELS=("qwen3:8b" "qwen3:4b"); WHISPER_MODELS=("small" "medium") ;;
    --no-models) PULL_MODELS=0 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done
EMBED_MODEL="bge-m3"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

step "Python 3.11"
PY=""
for cand in python3.11 "$HOME/.local/bin/python3.11" /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
[ -n "$PY" ] || fail "нужен Python 3.11 (macOS: brew install python@3.11; Ubuntu: apt install python3.11 python3.11-venv)"
echo "найден $PY"

step "Виртуальное окружение .venv и зависимости"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -m pip check

step "Демо-БД (20 вымышленных номеров)"
.venv/bin/python -m storage.seed_demo

step "Проверка базы схем"
.venv/bin/python data/schemes/validate.py | tail -2

if [ "$PULL_MODELS" = 0 ]; then
  echo; echo "Готово (без моделей). Запуск: scripts/run.sh"; exit 0
fi

step "Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin)
      command -v brew >/dev/null 2>&1 || fail "поставьте Homebrew (https://brew.sh) или Ollama вручную: https://ollama.com/download"
      brew install ollama ;;
    Linux)
      curl -fsSL https://ollama.com/install.sh | sh ;;
    *) fail "неизвестная ОС, поставьте Ollama вручную: https://ollama.com/download" ;;
  esac
fi
ollama --version

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "запускаю ollama serve в фоне"
  nohup ollama serve >/tmp/ollama-antifraud.log 2>&1 &
  for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || fail "Ollama не отвечает, см. /tmp/ollama-antifraud.log"
fi

step "Модели Ollama: ${LLM_MODELS[*]} $EMBED_MODEL"
for m in "${LLM_MODELS[@]}" "$EMBED_MODEL"; do ollama pull "$m"; done

step "Модели Whisper: ${WHISPER_MODELS[*]}"
for m in "${WHISPER_MODELS[@]}"; do
  .venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('$m', device='cpu', compute_type='int8'); print('$m — ок')"
done
.venv/bin/python -c "from silero_vad import load_silero_vad; load_silero_vad(onnx=True); print('silero-vad — ок')"

step "Проверка распознавания на data/calls/011.wav"
HF_HUB_OFFLINE=1 .venv/bin/python -m audio.listen --file data/calls/011.wav --model "${WHISPER_MODELS[0]}" | head -2

echo
echo "Готово. Запуск: scripts/run.sh"
