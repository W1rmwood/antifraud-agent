import { useEffect, useState } from 'react'
import { clock, KINDS, ms, plural, seconds, TACTICS } from '../format.js'
import { Alert, ChevronDown, ChevronLeft, ChevronRight, Database, Doc, Message, PhoneOff, StageIcon, Steps, Target } from '../icons.jsx'
import { jumpToLine } from './CallTimeline.jsx'

/** Вердикт и безопасный выход: главное, что человек должен увидеть во время звонка. */
export function Verdict({ view, live }) {
  const { verdict, scheme, alarms, warnings, hangup } = view
  if (verdict === 'none')
    return (
      <section className="verdict verdict-none" key="none">
        <header className="verdict-head">
          <span className={`dot ${live || view.ended ? 'dot-ok' : 'dot-dead'}`} />
          <span className="verdict-title">{live ? 'Признаков мошенничества нет' : view.ended ? 'Признаков мошенничества не было' : 'Звонок не разобран'}</span>
        </header>
        <p className="verdict-text">
          {live ? 'Агент слушает разговор и предупредит, если появятся признаки развода.' : view.ended ? 'Агент не нашёл ни требований, ни признаков развода.' : 'Агент остановился раньше, чем успел что-то понять.'}
        </p>
      </section>
    )
  const history = warnings.find((w) => w.kind === 'history')
  const title =
    verdict === 'scheme'
      ? 'Похоже на мошенничество'
      : alarms.length
        ? 'Есть признаки мошенничества'
        : history?.schemes.length
          ? 'С этого номера уже звонили мошенники'
          : 'Номер уже звонил'
  // key по вердикту: карточка появляется заново, когда агент повышает уровень тревоги.
  return (
    <section className={`verdict verdict-${verdict}`} key={verdict}>
      <header className="verdict-head">
        <Alert />
        <span className="verdict-title">{title}</span>
        {scheme && <span className="verdict-conf">{Math.round(scheme.confidence * 100)}%</span>}
      </header>
      {scheme ? (
        <p className="verdict-text">{scheme.scheme_name}</p>
      ) : alarms.length ? (
        <p className="verdict-text">{alarms.at(-1).labels.join(', ')}</p>
      ) : null}
      {hangup && <p className="verdict-exit" key={hangup.text}>{hangup.text}</p>}
      {(scheme || alarms.length > 0) && <p className="verdict-note">Ничего не называйте и не переводите.</p>}
    </section>
  )
}

/** «Что сказать»: подсказки агента, можно листать уже пришедшие. */
export function Hint({ hints, showProvoke }) {
  const shown = showProvoke ? hints : hints.filter((h) => h.kind !== 'provoke')
  const hidden = hints.length - shown.length
  // Номер выбранной подсказки; null — следить за последней. Пришла новая — снова показываем её.
  // Индекс считается из списка, а не хранится: если подсказки пришли разом (открыли законченный
  // звонок), он не может указать мимо списка.
  const [pinned, setPinned] = useState(null)
  useEffect(() => setPinned(null), [shown.length])
  const i = pinned != null && pinned < shown.length ? pinned : shown.length - 1
  const setI = setPinned
  const hint = shown[i]
  return (
    <section className={`card hint ${hint ? 'hint-on' : ''}`}>
      <header className="card-head">
        <Message />
        <h3>Что сказать</h3>
        {hint && (
          <span className="chip chip-agent">
            {KINDS[hint.kind]}{hint.tactic && TACTICS[hint.tactic] ? `: ${TACTICS[hint.tactic]}` : ''}
          </span>
        )}
      </header>
      {hint ? (
        <p className="hint-text" key={`${i}-${hint.text}`}>{hint.text}</p>
      ) : (
        <p className="muted">Подсказка появится, когда агент поймёт, что за схема.</p>
      )}
      {shown.length > 1 && (
        <footer className="pager">
          <span>{i === shown.length - 1 ? 'Последняя подсказка' : `Пришла в ${clock(hint?.at)}`}</span>
          <button className="icon-btn" onClick={() => setI(Math.max(0, i - 1))} disabled={i <= 0} aria-label="Предыдущая подсказка"><ChevronLeft /></button>
          <span className="pager-n">{i + 1} из {shown.length}</span>
          <button className="icon-btn" onClick={() => setI(Math.min(shown.length - 1, i + 1))} disabled={i >= shown.length - 1} aria-label="Следующая подсказка"><ChevronRight /></button>
        </footer>
      )}
      {hidden > 0 && <p className="muted small">Грубых подсказок скрыто: {hidden}. Включаются в настройках нового звонка.</p>}
    </section>
  )
}

/** Этапы схемы на линии: пройденные, текущий, впереди. */
export function Stepper({ stages, current, note }) {
  return (
    <ol className="stepper">
      {stages.map((s) => {
        const state = current == null ? 'todo' : s.n < current ? 'done' : s.n === current ? 'current' : 'todo'
        return (
          <li key={s.n} className={`step step-${state}`}>
            <StageIcon state={state} />
            <span className="step-name">{s.name}</span>
            {s.n === current && note && <span className="step-note">{note}</span>}
          </li>
        )
      })}
    </ol>
  )
}

/** Схема и этапы: где мошенник сейчас и что сделает дальше. */
export function Scheme({ view, stages }) {
  const { scheme, stage } = view
  if (!scheme)
    return (
      <section className="card">
        <header className="card-head"><Target /><h3>Схема</h3></header>
        <p className="muted">Не определена. Агент сравнивает разговор с 32 известными схемами.</p>
      </section>
    )
  const current = stage?.scheme_id === scheme.scheme_id ? stage.stage_n : null
  const pct = Math.round(scheme.confidence * 100)
  return (
    <section className="card" key={scheme.scheme_id}>
      <header className="card-head">
        <Target />
        <h3>{scheme.scheme_name}</h3>
        <a className="card-link" href={`#/schemes/${scheme.scheme_id}`}>Карточка</a>
      </header>
      <div className="conf">
        <div className="conf-bar"><span style={{ width: `${pct}%` }} /></div>
        <span>уверенность {pct}%</span>
      </div>
      <Stepper stages={stages} current={current} />
      {stage?.next_move && current && (
        <p className="next" key={stage.stage_n}><span className="next-label">Дальше</span><span>{stage.next_move}</span></p>
      )}
    </section>
  )
}

const WARNING_TITLE = { rule: 'Признак развода', demand: 'Требование', other: 'Предупреждение' }

export function Signs({ warnings }) {
  const alarms = warnings.filter((w) => w.kind !== 'history')
  return (
    <section className="card">
      <header className="card-head"><Alert /><h3>Признаки</h3>{alarms.length > 0 && <span className="badge">{alarms.length}</span>}</header>
      {alarms.length === 0 ? (
        <p className="muted">Пока ни одного.</p>
      ) : (
        <ol className="signs">
          {alarms.map((w, k) => (
            <li key={k}>
              <button className="sign" onClick={() => w.lineSeq != null && jumpToLine(w.lineSeq)} disabled={w.lineSeq == null}>
                <span className="time">{clock(w.phraseAt ?? w.at)}</span>
                <span>
                  <b>{w.kind === 'rule' ? w.labels.join(', ') : WARNING_TITLE[w.kind]}</b>
                  {w.quotes.length > 0 && <q>{w.quotes.at(-1)}</q>}
                  {w.kind === 'other' && <span className="muted">{w.text}</span>}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function logRow(e) {
  switch (e.kind) {
    case 'tool': {
      if (!e.ok) return [Database, <>Инструмент <code>{e.tool}</code> не ответил, звонок продолжается</>, null, 'muted']
      if (e.tool === 'lookup_number') {
        const n = e.result?.calls_count ?? 0
        return [Database, <>Проверил номер: {n ? `звонил ${n} ${plural(n, ['раз', 'раза', 'раз'])}` : 'раньше не звонил'}</>, ms(e.ms)]
      }
      if (e.tool === 'get_scheme') return [Database, <>Открыл карточку схемы</>, ms(e.ms)]
      if (e.tool === 'save_case') return [Database, <>Сохранил кейс в базу</>, ms(e.ms)]
      return [Database, <><code>{e.tool}</code></>, ms(e.ms)]
    }
    case 'warning': {
      const w = e.w
      if (w.kind === 'rule') return [Alert, <>Правило: <b>{w.labels.join(', ')}</b></>, w.lag != null ? `через ${seconds(w.lag)} после фразы` : null, 'red']
      if (w.kind === 'demand') return [Alert, <>Модель: <b>новое требование</b></>, w.lag != null ? `через ${seconds(w.lag)} после фразы` : null, 'red']
      if (w.kind === 'history') return [Alert, <>Номер уже звонил {w.count} {plural(w.count, ['раз', 'раза', 'раз'])}</>, null, 'red']
      return [Alert, <>{w.text}</>, null, 'red']
    }
    case 'scheme':
      return [Target, <>Определил схему <b>{e.scheme.scheme_name}</b>, {Math.round(e.scheme.confidence * 100)}%</>,
        e.scheme.sinceAlarm != null ? `через ${seconds(e.scheme.sinceAlarm)} после первого признака` : null, 'agent']
    case 'stage':
      return [Steps, <>Этап {e.stage.stage_n}: {e.stage.stage_name}</>, null]
    case 'suggestion':
      return e.suggestion.kind === 'hangup'
        ? [PhoneOff, <>Подсказал выход: положить трубку</>, null]
        : [Message, <>Подсказка: {KINDS[e.suggestion.kind]}{e.suggestion.tactic && TACTICS[e.suggestion.tactic] ? `, ${TACTICS[e.suggestion.tactic]}` : ''}</>, null, 'agent']
    case 'summary':
      return [Doc, <>Написал отчёт</>, null]
    case 'ended':
      return [PhoneOff, <>Звонок закончен</>, null]
  }
  return null
}

/** Лента действий агента: инструменты MCP, правила, разбор моделью — с таймингами. */
export function AgentLog({ log, open: startOpen = false, replay = false }) {
  const [open, setOpen] = useState(startOpen)
  const rows = log.map((e) => [e, logRow(e)]).filter(([, row]) => row)
  const last = rows.at(-1)
  return (
    <section className="card log">
      <button className="card-head card-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Steps />
        <h3>Действия агента</h3>
        <span className="badge">{rows.length}</span>
        {/* Свёрнутая лента показывает, что агент сделал только что. */}
        {!open && last && <span className="log-last" key={rows.length}>{last[1][1]}</span>}
        <ChevronDown className={open ? 'rot' : ''} />
      </button>
      {open && replay && (
        <p className="muted small">Это запись: паузы длиннее 15 с сокращены, при ускорении — все. Настоящие задержки агента видны в живом звонке.</p>
      )}
      {open && (
        <ol className="activity">
          {rows.map(([e, [Icon, text, extra, tone]], k) => (
            <li key={k} className={tone ? `tone-${tone}` : ''}>
              <Icon size={14} />
              <span className="activity-text">{text}{extra && <span className="muted"> {extra}</span>}</span>
              <span className="time">{clock(e.at)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
