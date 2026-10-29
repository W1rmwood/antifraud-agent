import { useMemo, useState } from 'react'
import { api, mergeHistory, useCallStream, useLoad } from '../api.js'
import { callView } from '../callView.js'
import { phone as fmtPhone, plural, TAGS, when } from '../format.js'
import { Search } from '../icons.jsx'
import Report from './Report.jsx'

function useSchemeNames() {
  const [schemes] = useLoad(api.schemes, [])
  return useMemo(() => Object.fromEntries((schemes || []).map((s) => [s.id, s])), [schemes])
}

const monthFmt = new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' })
const monthOf = (iso) => {
  const d = new Date(iso)
  const label = monthFmt.format(d).replace(' г.', '')
  const name = d.getFullYear() === new Date().getFullYear() ? label.split(' ')[0] : label
  return name[0].toUpperCase() + name.slice(1)
}

/** Звонки по месяцам, как список задач в Linear: заголовок группы и счётчик. */
function CaseRows({ cases, schemes }) {
  if (!cases.length) return <p className="empty">Звонков пока нет.</p>
  const groups = []
  for (const c of cases) {
    const m = monthOf(c.started_at)
    if (groups.at(-1)?.[0] !== m) groups.push([m, []])
    groups.at(-1)[1].push(c)
  }
  return groups.map(([month, list]) => (
    <section key={month}>
      <header className="month"><h2>{month}</h2><span className="count">{list.length} {plural(list.length, ['звонок', 'звонка', 'звонков'])}</span></header>
      <Rows cases={list} schemes={schemes} />
    </section>
  ))
}

function Rows({ cases, schemes }) {
  return (
    <ul className="rows cases">
      {cases.map((c) => {
        const s = c.scheme_id ? schemes[c.scheme_id] : null
        return (
          <li key={c.call_id}>
            <a className="row" href={c.noReport ? `#/call/${c.call_id}` : `#/case/${c.call_id}`}>
              <span className={`dot ${c.scheme_id ? 'dot-red' : c.noReport ? '' : 'dot-ok'}`} />
              <span className="row-phone mono">{c.phone ? fmtPhone(c.phone) : 'номер неизвестен'}</span>
              <span className="row-title">
                <span>{c.noReport ? (c.running ? 'Идёт сейчас' : c.ended ? 'Без отчёта' : 'Прервался') : c.scheme_id ? s?.name || c.scheme_id : 'Обычный звонок'}</span>
                {c.replay && <span className="chip chip-inline">запись</span>}
              </span>
              <span className="row-meta">
                {c.max_stage && s ? `этап ${c.max_stage} из ${s.stages.length}` : ''}
              </span>
              <span className="row-meta row-legend">{c.legend && c.scheme_id ? c.legend : ''}</span>
              <span className="row-date">{when(c.started_at)}</span>
            </a>
          </li>
        )
      })}
    </ul>
  )
}

function SearchBox({ initial = '' }) {
  const [q, setQ] = useState(initial)
  return (
    <form className="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) location.hash = `#/history/${encodeURIComponent(q.trim())}` }}>
      <Search />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти по номеру: 8 999 000-00-01" aria-label="Номер телефона" />
    </form>
  )
}

export function History() {
  const [cases, error] = useLoad(() => Promise.all([api.cases(200).catch(() => []), api.runs()]).then(([c, r]) => mergeHistory(c, r)), [])
  const schemes = useSchemeNames()
  return (
    <>
      <header className="topbar"><h1>История</h1><SearchBox /></header>
      <div className="page">
        {error && <p className="error">{error.message}</p>}
        {cases && (
          <>
            <p className="page-note">{cases.length} {plural(cases.length, ['звонок', 'звонка', 'звонков'])}, новые сверху. Кейс сохраняет агент в конце звонка; записи, проигранные без модели, помечены.</p>
            <CaseRows cases={cases} schemes={schemes} />
          </>
        )}
      </div>
    </>
  )
}

export function NumberPage({ phone }) {
  const [data, error] = useLoad(() => api.number(phone), [phone])
  const schemes = useSchemeNames()
  const info = data?.info
  return (
    <>
      <header className="topbar">
        <nav className="crumbs"><a href="#/history">История</a><span className="crumb-sep">/</span><h1>{info ? fmtPhone(info.phone) : phone}</h1></nav>
        <SearchBox initial={phone} />
      </header>
      <div className="page">
        {error && <p className="error">{error.message}</p>}
        {info && (
          <>
            <section className="number-card">
              <p className="number-big mono">{fmtPhone(info.phone)}</p>
              {info.calls_count === 0 ? (
                <p className="muted">С этого номера не звонили. Если позвонит — агент проверит его в начале звонка.</p>
              ) : (
                <ul className="props">
                  <li><span>Звонков</span>{info.calls_count}</li>
                  <li><span>Первый</span>{when(info.first_seen)}</li>
                  <li><span>Последний</span>{when(info.last_seen)}</li>
                  {info.tags.length > 0 && (
                    <li><span>Метки</span><span className="chips">{info.tags.map((t) => <span key={t} className={`chip ${t === 'fraud' ? 'chip-red' : ''}`}>{TAGS[t] || t}</span>)}</span></li>
                  )}
                  {info.schemes.length > 0 && (
                    <li><span>Схемы</span><span className="chips">{info.schemes.map((id) => <a key={id} className="chip" href={`#/schemes/${id}`}>{schemes[id]?.name || id}</a>)}</span></li>
                  )}
                </ul>
              )}
            </section>
            <CaseRows cases={data.cases} schemes={schemes} />
          </>
        )}
      </div>
    </>
  )
}

/** Кейс из истории: если лог звонка на диске — полный отчёт со стенограммой, иначе — то, что в базе. */
export function CasePage({ callId }) {
  const stream = useCallStream(callId)
  const view = useMemo(() => callView(stream.events), [stream.events])
  const [cases] = useLoad(() => (stream.missing ? api.cases(500).catch(() => []) : Promise.resolve(null)), [stream.missing])
  const schemes = useSchemeNames()
  const caseData = view.summary || cases?.find((c) => c.call_id === callId)
  const scheme = caseData?.scheme_id ? schemes[caseData.scheme_id] : null
  return (
    <>
      <header className="topbar">
        <nav className="crumbs"><a href="#/history">История</a><span className="crumb-sep">/</span><h1>{caseData?.phone ? fmtPhone(caseData.phone) : callId}</h1></nav>
      </header>
      {!caseData && stream.done && cases && <p className="empty-page">Кейса {callId} нет ни в логах, ни в базе.</p>}
      {caseData && (
        <>
          {!view.summary && <p className="banner">Лога этого звонка нет на диске — показано то, что агент сохранил в базу.</p>}
          <Report view={view.summary ? view : null} caseData={caseData} stages={view.stages.length ? view.stages : scheme?.stages || []} schemeName={scheme?.name}
            replay={Boolean(view.summary && view.summary.call_id !== view.callId)} />
        </>
      )}
    </>
  )
}
