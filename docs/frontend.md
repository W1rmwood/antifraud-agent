# Интерфейс «Антимошенника»: как стыковаться с бэкендом

## Готовый интерфейс: веб (`ui/web` + `ui/server.py`)

Вместо Streamlit сделан веб-интерфейс: React (Vite) в `ui/web` и HTTP-мост `ui/server.py` поверх
`agent/ui_api.py`. Мост — только стандартная библиотека Python, `requirements.txt` не менялся.

```bash
cd ui/web && npm install && npm run build      # один раз и после правок в ui/web/src (нужен Node 20+)
cd ../.. && .venv/bin/python -m ui.server      # http://127.0.0.1:8642
```

Разработка фронта с горячей перезагрузкой: мост как выше, плюс `cd ui/web && npm run dev` — vite проксирует `/api` на 8642.

Как устроено: мост делает то, что ниже делает Streamlit, — `start_call` / `start_replay` / `stop`, и отдаёт
лог `data/runs/<call_id>.jsonl` в браузер потоком Server-Sent Events (события как есть, контракт №3).
Состояние экрана браузер собирает сам: `ui/web/src/callView.js` повторяет `call_view`.

| Адрес | Что делает |
|---|---|
| `GET /api/sources` | тестовые звонки (`list_test_calls`) и записи для проигрывания; идёт ли звонок |
| `POST /api/calls` | старт: `{source: file\|mic\|loopback\|replay, path?, phone?, realtime?, speed?}`; второй звонок — 409 |
| `POST /api/calls/<id>/stop` | «Закончить» (в фоне, до 45 с) |
| `GET /api/calls/<id>/events` | поток событий; для упавшего звонка — причина из `<id>.stderr.log` |
| `POST /api/upload?name=` | запись звонка из браузера → `data/uploads/` (wav, flac, ogg, mp3, txt) |
| `GET /api/runs` | звонки этого компьютера из `data/runs/` — левая панель и история |
| `GET /api/cases`, `/api/numbers/<phone>` | история из базы (`recent_cases`, `number_history`) |
| `GET /api/schemes`, `/api/schemes/<id>` | каталог и карточка схемы |
| `GET /api/health` | готов ли компьютер к звонку через модель: зависимости, Ollama и модели, индекс, база, Git LFS |

Экраны: новый звонок (слушать вживую, загрузить запись, тестовые звонки, демо без модели), звонок
(разговор с подсветкой фраз, на которых сработали правила и модель, полоса звонка, вердикт и выход,
«что сказать», этапы, признаки, действия агента с таймингами), отчёт, история с поиском по номеру, схемы.
Если агент упал, экран называет причину по stderr и даёт команду, которая её исправит.

Тесты моста: `.venv/bin/python -m pytest tests/test_ui_server.py` (без модели — звонки идут проигрыванием).

Ниже — исходное описание фасада для Streamlit; всё в нём по-прежнему верно, веб-мост пользуется тем же `ui_api`.

Документ для того, кто делает `ui/app.py` (Streamlit). Всё, что нужно от бэкенда, — в модуле
`agent/ui_api.py`; импортировать граф, LLM или звук не нужно.

## Как это устроено

```
 ui/app.py ──start_call()──▶ процесс python -m agent.run …  ──пишет──▶ data/runs/<call_id>.jsonl
     ▲                                                                       │
     └──────────── EventTail.read_new() + call_view() ◀──────────────────────┘
```

- Звонок идёт **отдельным процессом** (кнопка «Старт» → `start_call`, «Стоп» → `call.stop()`).
  Streamlit перезапускает скрипт на каждое действие — держать агента внутри него нельзя.
- Процесс пишет **события** в `data/runs/<call_id>.jsonl`: одна строка — одно событие (контракт №3).
  UI читает хвост этого файла. Файл дописывается сразу после каждого события.
- **Проигрывание** записанного звонка (`start_replay`) пишет в тот же файл и с теми же паузами — экраны
  не отличают живой звонок от записи. Разрабатывать UI можно целиком без модели.

## Скелет приложения

```python
import streamlit as st

from agent.ui_api import EventTail, call_view, list_test_calls, start_call, start_replay

st.set_page_config(page_title="Антимошенник", layout="wide")
state = st.session_state  # переживает перезапуски скрипта Streamlit

with st.sidebar:
    mode = st.radio("Источник", ["Файл", "Микрофон", "Запись (без модели)"])
    calls = list_test_calls()
    chosen = st.selectbox("Звонок", calls, format_func=lambda c: c.title) if mode == "Файл" else None
    if st.button("Старт", disabled="call" in state and state.call.running):
        if mode == "Файл":
            state.call = start_call(source="file", path=chosen.path, phone=chosen.phone)
        elif mode == "Микрофон":
            state.call = start_call(source="mic")
        else:
            state.call = start_replay("data/runs/demo.jsonl")
        state.tail = EventTail(state.call.events_path)
    if "call" in state and state.call.running and st.button("Стоп"):
        state.call.stop()  # до ~45 с: агент дописывает отчёт и сохраняет кейс


@st.fragment(run_every=0.5)  # перерисовывается только этот блок, раз в полсекунды
def live() -> None:
    if "tail" not in state:
        st.info("Выберите звонок и нажмите «Старт»")
        return
    state.tail.read_new()
    view = call_view(state.tail.events)
    left, right = st.columns([3, 2])
    with left:
        for line in view.transcript:
            st.chat_message("assistant" if line.speaker == "caller" else "user").write(line.text)
    with right:
        if view.hangup:
            st.error(view.hangup.text)
        for w in view.warnings[-3:]:
            st.warning(w)
        if view.scheme:
            st.subheader(view.scheme.scheme_name)
            st.progress(view.scheme.confidence, text=f"уверенность {view.scheme.confidence:.0%}")
            for s in view.stages:
                current = view.stage and s.n == view.stage.stage_n
                st.markdown(f"**▶ {s.n}. {s.name}**" if current else f"{s.n}. {s.name}")
            if view.stage:
                st.caption(f"Дальше: {view.stage.next_move}")
        if view.suggestion:
            st.markdown(f"### {view.suggestion.text}")
    if view.summary:
        st.success(view.summary.summary)
    if "call" in state and not state.call.running and not view.ended:
        st.error("Звонок прервался:\n" + state.call.error_text())


live()
```

Запуск: `.venv/bin/python -m streamlit run ui/app.py`. Перед этим должен работать `ollama serve`
(для режима «Запись» — не нужен).

## `agent/ui_api.py` — что там есть

| Функция / класс | Для чего |
|---|---|
| `start_call(source="file"\|"mic"\|"loopback", path=None, phone=None, call_id=None, realtime=True) -> CallProcess` | «Старт». Для файла `realtime=True` выдаёт реплики в темпе живого звонка |
| `start_replay(events_file, call_id=None, speed=1.0) -> CallProcess` | проиграть записанный звонок (`speed=2` — вдвое быстрее) |
| `CallProcess.call_id`, `.events_path` | где лежит лог этого звонка |
| `CallProcess.running`, `.returncode` | идёт ли ещё; `0` — закончился штатно, другое — упал |
| `CallProcess.stop(timeout_s=45)` | «Стоп»: штатное завершение (SIGTERM), не успел — kill |
| `CallProcess.error_text()` | последние строки ошибки, если процесс упал |
| `EventTail(path).read_new() -> list[AgentEvent]` | дочитать новые события; `.events` — все, `.finished` — пришёл `call_ended` |
| `read_events(path)` | прочитать готовый лог целиком |
| `call_view(events) -> CallView` | **готовое состояние экрана** (см. ниже) |
| `list_test_calls() -> list[CallFile]` | звонки для выбора: `.path`, `.title`, `.phone`, `.scheme_id`, `.kind` |
| `get_scheme(scheme_id) -> Scheme` | схема целиком: этапы, `stall_tactics`, легенда — для подробной карточки |
| `recent_cases(limit=20) -> list[CallCase]` | экран «История»: последние кейсы |
| `number_history(phone) -> (NumberInfo, list[CallCase])` | поиск по номеру; неверный номер — `ValueError` |

### `CallView` — что рисовать

| Поле | Тип | На экране |
|---|---|---|
| `phone`, `source` | `str \| None`, `dict` | шапка: номер или «номер неизвестен», источник |
| `transcript` | `list[Line(speaker, text, t_start)]` | лента реплик; `speaker`: `caller` — звонящий, `user` — абонент, `unknown` — живой звук, роли не разделены |
| `verdict` | `"scheme" \| "warning" \| "none"` | цвет карточки: схема определена / есть тревожные признаки / ничего |
| `scheme` | `scheme_id, scheme_name, confidence (0–1), evidence: [str]` | карточка схемы, фразы-доказательства |
| `stages` | `list[StageInfo(n, name)]` | прогресс по этапам |
| `stage` | `stage_n, stage_name, next_move` | текущий этап (подсветить) и «что будет дальше» |
| `warnings` | `list[str]` | красные плашки, по порядку |
| `hangup` | `SuggestionPayload \| None` | «Положите трубку и перезвоните…» — показывать всегда, когда есть |
| `suggestion` | `SuggestionPayload(text, kind, tactic) \| None` | крупно: что сказать мошеннику (`kind`: `stall`, `question`, `provoke`) |
| `suggestions` | `list[SuggestionPayload]` | все подсказки, если нужна история |
| `tools` | `list[ToolCallPayload \| ToolResultPayload]` | сворачиваемый лог действий агента |
| `summary` | `CallCase \| None` | экран «Отчёт»: `legend`, `key_phrases`, `summary`, `scheme_id`, `max_stage` |
| `ended` | `bool` | звонок закончен |

`call_view` каждый раз собирает состояние из всех событий заново — его можно вызывать на каждой
перерисовке, это дёшево (сотни событий на звонок).

## События (контракт №3)

Каждая строка лога — `AgentEvent`: `{call_id, seq, ts, type, payload}`. `seq` — номер события в звонке
с 0 без пропусков; `ts` — время UTC. Типизированный payload — `contracts.parse_payload(event)`.

| type | payload | Когда приходит | Что показать |
|---|---|---|---|
| `call_started` | `phone`, `source` | первым | шапка звонка |
| `tool_call` / `tool_result` | `tool`, `args` / `tool`, `ok`, `result` | `lookup_number` в начале (если номер известен), `get_scheme` при объявлении схемы, `save_case` в конце | лог инструментов; `ok=false` — серым «инструмент не ответил», звонок продолжается |
| `transcript` | `Utterance`: `seq`, `speaker`, `text`, `t_start`, `t_end` | через ~2 с после конца фразы | лента |
| `warning` | `text` | сразу после фразы с признаком развода (правила, без модели) или после разбора моделью; в начале — если номер уже звонил | красная плашка |
| `suggestion` с `kind="hangup"` | `text` | при первом признаке развода, при объявлении схемы, при каждом новом требовании мошенника | «Положите трубку…» — заметно, но не крупнее подсказки |
| `scheme_detected` | `scheme_id`, `scheme_name`, `confidence`, `evidence`, `stages` | после разбора моделью (обычно 10–20 с после первого требования); может прийти ещё раз, если схема сменилась | карточка схемы; при новом — заменить карточку и сбросить этап |
| `stage_changed` | `scheme_id`, `stage_n`, `stage_name`, `next_move` | после `scheme_detected` и при переходе этапа (этап может вернуться назад) | подсветить этап, «дальше: …» |
| `suggestion` | `text`, `kind` (`stall` / `question` / `provoke`), `tactic` | в фоне, после разбора | крупно: что сказать |
| `summary` | `CallCase` | в конце | экран отчёта |
| `call_ended` | `{}` | **последним**, всегда, если процесс закончился штатно | звонок окончен |

### Гарантии

- `call_started` — первое событие, `call_ended` — последнее; между `summary` и `call_ended` — только `save_case`.
- Реплики `transcript` идут по порядку, `payload.seq` — без пропусков.
- **На обычном звонке нет ни `scheme_detected`, ни `suggestion`, ни `warning`** (кроме предупреждения об истории номера).
- После `scheme_detected` новая схема может прийти ещё раз только если она уверенно обошла прежнюю или выиграла
  два разбора подряд.
- Если процесс упал (`call.running == False` и `call.returncode != 0`), `call_ended` не придёт — покажите
  `call.error_text()`.

## Мок-события для разработки

| Файл | Что внутри |
|---|---|
| `data/mock_events/fraud_relative.jsonl` | «мама, я попал в аварию»: схема, этапы, плашки, ВЫХОД, подсказки. Записан с живой моделью в реальном времени (30.10) |
| `data/mock_events/fraud_bank_call.jsonl` | мошенник «из ЦБ» (звонок 001, безопасный счёт). Агент здесь **ошибся схемой** — показал «Взлом аккаунта Госуслуг»; для UI это не важно, но не берите его как пример правильного ответа |
| `data/mock_events/no_fraud.jsonl` | настоящий звонок из банка: ни схемы, ни плашек, ни подсказок |
| `data/mock_events/example.jsonl` | эталон Александра — все 10 типов событий (старый формат: без `seq` и `stages`, их добирает `call_view`) |
| `data/runs/demo.jsonl` | главный демо-звонок — запасной вариант на защите (сейчас — копия `fraud_relative`) |

Проиграть в терминале, чтобы посмотреть темп: `.venv/bin/python -m agent.replay data/mock_events/fraud_relative.jsonl --call-id try`
— события появятся в `data/runs/try.jsonl`.

## Запуск без фасада (контракт №7)

```bash
.venv/bin/python -m agent.run --call-id c001 --source file --path data/calls/001.wav --phone +79990000001 --realtime --json
```

`--source mic` и `--source loopback` — живой звук (`--path` не нужен). SIGTERM или Ctrl+C — штатное
завершение: дорабатывает разбор, пишет `summary` и `call_ended`. Папку логов задаёт `ANTIFRAUD_RUNS_DIR`.

## Подводные камни

- **Один звонок за раз.** Модель одна; два звонка параллельно будут ждать друг друга.
- **«Стоп» не мгновенный**: до ~45 с агент дописывает отчёт. Показывайте «завершаю…», пока `call.running`.
- **Микрофон**: при первом запуске macOS спросит разрешение — для процесса, в котором запущен Streamlit
  (терминал). Без разрешения фраз не будет совсем.
- **`unknown` вместо `caller`/`user`** в живом режиме — нормально: в живом звуке один канал.
- **Подсказки `provoke` бывают грубыми** (режим провокации) — это решение по ТЗ ещё обсуждается;
  показывайте `kind`, чтобы их можно было скрыть настройкой.
- В `data/runs/` рядом с логом лежит `<call_id>.stderr.log` — первое, что смотреть, если звонок упал.
