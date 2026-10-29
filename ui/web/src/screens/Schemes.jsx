import { useMemo, useState } from 'react'
import { api, useLoad } from '../api.js'
import { CATEGORIES, plural } from '../format.js'
import { External, Search } from '../icons.jsx'

export function Schemes() {
  const [schemes, error] = useLoad(api.schemes, [])
  const [q, setQ] = useState('')
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const found = (schemes || []).filter((s) =>
      !needle || [s.name, s.legend, ...s.aliases].some((t) => t.toLowerCase().includes(needle)),
    )
    return Object.keys(CATEGORIES).map((cat) => [cat, found.filter((s) => s.category === cat)]).filter(([, list]) => list.length)
  }, [schemes, q])

  return (
    <>
      <header className="topbar">
        <h1>Схемы</h1>
        <div className="search">
          <Search />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти: «безопасный счёт», «внук», «Госуслуги»" aria-label="Поиск по схемам" />
        </div>
      </header>
      <div className="page">
        {error && <p className="error">{error.message}</p>}
        {schemes && <p className="page-note">По этим схемам агент узнаёт мошенника: каждая — легенда и этапы с типичными фразами.</p>}
        {groups.map(([cat, list]) => (
          <section key={cat} className="group">
            <header className="group-head"><h2>{CATEGORIES[cat]}</h2><span className="count">{list.length}</span></header>
            <ul className="rows">
              {list.map((s) => (
                <li key={s.id}>
                  <a className="row" href={`#/schemes/${s.id}`}>
                    <span className="row-title">{s.name}</span>
                    <span className="row-meta row-legend">{s.legend}</span>
                    <span className="row-date">{s.stages.length} {plural(s.stages.length, ['этап', 'этапа', 'этапов'])}</span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {schemes && groups.length === 0 && <p className="empty">Ничего не нашлось.</p>}
      </div>
    </>
  )
}

// Адреса в punycode, которые браузер не раскодирует сам.
const HOSTS = { 'xn--b1aew.xn--p1ai': 'мвд.рф', 'xn--d1abbgf6aiiy.xn--p1ai': 'президент.рф' }
const host = (u) => {
  const h = new URL(u).hostname.replace(/^www\./, '')
  return HOSTS[h] || h
}

export function SchemeDetail({ id }) {
  const [s, error] = useLoad(() => api.scheme(id), [id])
  return (
    <>
      <header className="topbar">
        <nav className="crumbs"><a href="#/schemes">Схемы</a><span className="crumb-sep">/</span><h1>{s ? s.name : id}</h1></nav>
      </header>
      {error && <p className="error page">{error.message}</p>}
      {s && (
        <div className="scheme">
          <article className="scheme-main">
            <h2>{s.name}</h2>
            <p className="prose">{s.description}</p>

            <h3>Этапы</h3>
            <ol className="scheme-stages">
              {s.stages.map((st) => (
                <li key={st.n} data-n={st.n}>
                  <header><h4>{st.name}</h4></header>
                  <p className="muted">Цель: {st.goal}</p>
                  <ul className="phrases">{st.typical_phrases.map((p) => <li key={p}>{p}</li>)}</ul>
                  <div className="kv">
                    <span>Признаки</span>
                    <div className="chips">{st.red_flags.map((f) => <span key={f} className="chip chip-red">{f}</span>)}</div>
                  </div>
                  <div className="kv">
                    <span>Просят</span>
                    <div className="chips">{st.asks_for.map((f) => <span key={f} className="chip">{f}</span>)}</div>
                  </div>
                  <p className="next"><span>Дальше</span>{st.next_move}</p>
                </li>
              ))}
            </ol>

            <h3>Как тянуть время</h3>
            <ul className="tactics">{s.stall_tactics.map((t) => <li key={t}>{t}</li>)}</ul>
          </article>

          <aside className="scheme-side">
            <ul className="props">
              <li><span>Категория</span>{CATEGORIES[s.category]}</li>
              <li><span>Кем представляются</span>{s.legend}</li>
              <li><span>На что давят</span><span className="chips">{s.pressure_points.map((p) => <span key={p} className="chip">{p}</span>)}</span></li>
              {s.aliases.length > 0 && <li><span>Ещё называют</span><span>{s.aliases.join('; ')}</span></li>}
              <li>
                <span>Источники</span>
                <span className="sources">
                  {s.sources.map((u) => (
                    <a key={u} href={u} target="_blank" rel="noreferrer">{host(u)}<External size={12} /></a>
                  ))}
                </span>
              </li>
            </ul>
          </aside>
        </div>
      )}
    </>
  )
}
