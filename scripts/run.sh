#!/usr/bin/env bash
# Запуск интерфейса «Антимошенника» одной командой (macOS / Linux). Сеть не нужна.
# Агент и MCP-сервер стартуют из интерфейса по кнопке «Старт».
#
#   scripts/run.sh          # проверяет qwen3:8b
#   scripts/run.sh --lite   # проверяет qwen3:4b
set -euo pipefail

cd "$(dirname "$0")/.."

LLM_MODEL="${ANTIFRAUD_LLM:-qwen3:8b}"
[ "${1:-}" = "--lite" ] && LLM_MODEL="qwen3:4b"
EMBED_MODEL="bge-m3"
export ANTIFRAUD_LLM="$LLM_MODEL"
# Модели берутся только из локального кэша: никаких запросов к Hugging Face во время звонка.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

[ -x .venv/bin/python ] || fail "нет .venv — сначала scripts/install.sh"
command -v ollama >/dev/null 2>&1 || fail "Ollama не установлена — scripts/install.sh"

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama не запущена, запускаю ollama serve в фоне"
  nohup ollama serve >/tmp/ollama-antifraud.log 2>&1 &
  for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || fail "Ollama не отвечает, см. /tmp/ollama-antifraud.log"
fi

TAGS="$(curl -fsS http://127.0.0.1:11434/api/tags)"
for m in "$LLM_MODEL" "$EMBED_MODEL"; do
  printf '%s' "$TAGS" | grep -q "\"$m" || fail "модели $m нет в Ollama — ollama pull $m (нужен интернет)"
done
echo "Ollama: $LLM_MODEL и $EMBED_MODEL на месте"

[ -f data/antifraud.db ] || .venv/bin/python -m storage.seed_demo

exec .venv/bin/python -m streamlit run ui/app.py --server.headless=false
