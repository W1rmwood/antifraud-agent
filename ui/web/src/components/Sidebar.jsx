import { useEffect, useState } from 'react'
import { clock, phone as fmtPhone, when } from '../format.js'
import { Book, ChevronDown, History as HistoryIcon, Phone, Plus } from '../icons.jsx'

/** Цвет и подпись звонка в списках: схема, признаки, ничего, прервался. */
export function runState(r) {
  if (r.running) return ['live', r.scheme ? `${r.scheme.name}, ${Math.round(r.scheme.confidence * 100)}%` : r.warnings ? 'есть признаки' : 'агент слушает']
  if (!r.ended) return ['dead', r.lines ? 'прервался' : 'не запустился']
  const scheme = r.summary?.scheme_id ? r.scheme?.name || r.summary.legend || 'мошенничество' : null
  if (scheme) return ['red', scheme]
  return [r.warnings ? 'orange' : 'ok', r.warnings ? 'были признаки' : 'без мошенничества']
}

/** «только что», «12 мин назад», «в 21:40», «29 окт., 21:40». */
function ago(iso, now) {
  const min = Math.round((now - Date.parse(iso)) / 60000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} мин назад`
  return when(iso).replace('сегодня, ', 'в ')
}

function useNow(active) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

/** Карточка текущего или последнего звонка — открывает его экран. */
function CallCard({ run, on }) {
  const now = useNow(true)
  const [tone, label] = runState(run)
  return (
    <a href={`#/call/${run.call_id}`} className={`callcard callcard-${tone} ${on ? 'on' : ''}`} aria-current={on ? 'page' : undefined}>
      <span className="callcard-top">
        <span className={`dot dot-${tone}`} />
        {run.running ? 'Идёт звонок' : 'Последний звонок'}
      </span>
      <span className="callcard-phone">{run.phone ? fmtPhone(run.phone) : 'номер неизвестен'}</span>
      <span className="callcard-state">{label}{run.replay ? <span className="callcard-tag">запись</span> : null}</span>
      <span className="callcard-time">{run.running ? `идёт ${clock((now - Date.parse(run.started_at)) / 1000)}` : ago(run.last_at, now)}</span>
    </a>
  )
}

/** Готов ли компьютер к звонку через модель; раскрывается в список с тем, что сделать. */
function Readiness({ health }) {
  const [open, setOpen] = useState(false)
  if (!health) return null
  const ready = health.model_ready
  return (
    <section className="ready">
      <button className="ready-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`dot ${ready ? 'dot-ok' : 'dot-orange'}`} />
        <span>{ready ? 'Готов к звонку через модель' : 'Работает только запись'}</span>
        <ChevronDown className={open ? 'rot' : ''} size={14} />
      </button>
      {open && (
        <ul className="ready-list">
          <li><span className="dot dot-ok" /><span className="ready-label">Запись без модели<span className="ready-detail">готова</span></span></li>
          {health.checks.map((c) => (
            <li key={c.key}>
              <span className={`dot ${c.ok ? 'dot-ok' : 'dot-dead'}`} />
              <span className="ready-label">{c.label}<span className="ready-detail">{c.detail}</span></span>
              {!c.ok && <code className="ready-fix">{c.fix}</code>}
            </li>
          ))}
        </ul>
      )}
      <p className="ready-note">Работает на этом компьютере, без интернета</p>
    </section>
  )
}

export default function Sidebar({ route, runs, health, historyCount, schemesCount }) {
  const [page, arg] = route
  const current = runs.find((r) => r.running) || runs[0]
  const recent = runs.filter((r) => r !== current).slice(0, 4)
  const nav = [
    { href: '#/new', icon: Plus, label: 'Новый звонок', on: !page || page === 'new' },
    { href: '#/history', icon: HistoryIcon, label: 'История', on: ['history', 'case'].includes(page), count: historyCount },
    { href: '#/schemes', icon: Book, label: 'Схемы', on: page === 'schemes', count: schemesCount },
  ]
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark"><Phone size={14} /></span>
        Антимошенник
      </div>

      {current && <CallCard run={current} on={page === 'call' && arg === current.call_id} />}

      <nav className="nav">
        {current && (
          <a href={`#/call/${current.call_id}`} className={`nav-call ${page === 'call' ? 'on' : ''}`}>
            <Phone />{current.running ? 'Идёт звонок' : 'Последний звонок'}
            {current.running && <span className="dot dot-live" />}
          </a>
        )}
        {nav.map((n) => (
          <a key={n.href} href={n.href} className={n.on ? 'on' : ''} aria-current={n.on ? 'page' : undefined}>
            <n.icon />
            {n.label}
            {n.count != null && <span className="nav-count">{n.count}</span>}
          </a>
        ))}
      </nav>

      {recent.length > 0 && (
        <section className="recent">
          <h4>Недавние звонки</h4>
          <ul>
            {recent.map((r) => {
              const [tone, label] = runState(r)
              return (
                <li key={r.call_id}>
                  <a href={`#/call/${r.call_id}`} className={page === 'call' && arg === r.call_id ? 'on' : ''}>
                    <span className={`dot dot-${tone}`} />
                    <span className="recent-text">
                      <span className="mono">{r.phone ? fmtPhone(r.phone) : 'без номера'}</span>
                      <span className="recent-label">{label}</span>
                    </span>
                  </a>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <Readiness health={health} />
    </aside>
  )
}
