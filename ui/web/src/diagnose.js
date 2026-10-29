// Почему упал агент — по последним строкам его stderr. Человеку нужна причина и одна команда,
// а не трейсбек (он остаётся под катом).

const CAUSES = [
  [/No module named '([\w.]+)'/, (m) => [`Не установлены зависимости агента: нет модуля ${m[1]}.`, '.venv/bin/pip install -r requirements.txt']],
  [/Connect(ion)?Error|Connection refused|ConnectError|11434/, () => ['Не отвечает Ollama — модель не запущена.', 'ollama serve']],
  [/model ['"]?([\w.:-]+)['"]? not found/i, (m) => [`Модель ${m[1]} не скачана.`, `ollama pull ${m[1]}`]],
  [/chroma|Collection .* does not exist/i, () => ['Не собран индекс схем.', '.venv/bin/python -m rag.index']],
  [/PortAudio|Invalid input device|sounddevice|Error opening InputStream/i, () => ['Нет доступа к микрофону или звуку компьютера.', 'Системные настройки → Конфиденциальность → Микрофон: разрешить терминалу']],
  [/LocalEntryNotFoundError|offline mode|HF_HUB_OFFLINE|snapshot/i, () => ['Модель распознавания речи не скачана, а во время звонка интернет выключен.', 'скачать модель faster-whisper заранее, при включённом интернете']],
  [/Format not recognised|LibsndfileError|Error opening .*\.wav/i, () => ['Запись звонка не скачана: вместо .wav в папке лежит указатель Git LFS.', 'git lfs install && git lfs pull']],
  [/FileNotFoundError: .*?'([^']+)'/, (m) => [`Не найден файл ${m[1]}.`, 'git lfs pull — записи звонков лежат в Git LFS']],
]

export function diagnose(errorText) {
  for (const [re, explain] of CAUSES) {
    const m = re.exec(errorText || '')
    if (m) {
      const [reason, fix] = explain(m)
      return { reason, fix }
    }
  }
  return { reason: 'Агент завершился с ошибкой.', fix: null }
}
