import { useState } from 'react'
import { api, useLoad } from '../api.js'
import { callClock } from '../callView.js'
import { AgentLog } from '../components/AgentPanel.jsx'
import CallTimeline, { jumpToLine } from '../components/CallTimeline.jsx'
import Transcript from '../components/Transcript.jsx'
import { clock, duration, KINDS, phone as fmtPhone, plural, seconds, TACTICS, when } from '../format.js'
import { StageIcon } from '../icons.jsx'

const STATE_LABEL = { done: 'пройден', current: 'здесь остановился', todo: 'не дошёл' }

/** Этапы схемы: что мошенник успел, где остановился и что попросил бы дальше. */
function Scenario({ stages, reached }) {
  return (
    <ol className="scenario">
      {stages.map((s) => {
        const state = s.n < reached ? 'done' : s.n === reached ? 'current' : 'todo'
        return (
          <li key={s.n} className={`scenario-step scenario-${state}`}>
            <StageIcon state={state} />
            <div className="scenario-body">
              <div className="scenario-head">
                <span className="scenario-name">{s.name}</span>
                <span className="scenario-state">{STATE_LABEL[state]}</span>
              </div>
              {s.goal && <p className="muted">Цель: {s.goal}</p>}
              {s.asks_for?.length > 0 && state !== 'done' && (
                <div className="scenario-asks">
                  <span>{state === 'current' ? 'Просил' : 'Попросил бы'}</span>
                  <span className="chips">{s.asks_for.map((a) => <span key={a} className={`chip ${state === 'current' ? 'chip-red' : ''}`}>{a}</span>)}</span>
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function sourceLabel(source, replay) {
  if (replay) return 'запись, без модели'
  if (!source) return null
  return source.kind === 'file' ? source.path.split('/').pop() : source.kind === 'mic' ? 'микрофон' : 'звук компьютера'
}

/** Текст отчёта для буфера обмена: переслать родственникам или приложить к заявлению. */
function reportText({ c, title, fraud, reached, stages, demands, hangup, conf }) {
  const out = ['Отчёт о звонке — Антимошенник']
  out.push(`Номер: ${c.phone ? fmtPhone(c.phone) : 'неизвестен'}`)
  out.push(`Когда: ${when(c.started_at)}${c.ended_at ? `, длительность ${duration(c.started_at, c.ended_at)}` : ''}`)
  out.push(`Вывод: ${fraud ? `мошенничество — ${title}${conf ? `, уверенность ${conf}%` : ''}` : 'признаков мошенничества не найдено'}`)
  if (fraud && c.legend) out.push(`Кем представлялись: ${c.legend}`)
  if (c.summary) out.push('', `Кратко: ${c.summary}`)
  if (fraud && reached && stages[reached - 1]) out.push(`Мошенник дошёл до этапа ${reached} из ${stages.length}: ${stages[reached - 1].name}.`)
  if (demands.length) out.push('', 'Что требовал:', ...demands.map((d) => `— «${d.quote}»`))
  if (hangup) out.push('', `Что делать: ${hangup}`)
  return out.join('\n')
}

/**
 * Отчёт о звонке: итог, стенограмма, действия агента; справа — свойства звонка.
 * view — звонок из лога событий; caseData — кейс из базы, если лога на диске нет.
 */
export default function Report({ view, caseData, stages = [], schemeName, replay = false }) {
  const c = view?.summary || caseData
  const [tab, setTab] = useState('summary')
  const [copied, setCopied] = useState(false)
  const inLog = view?.schemeCard?.id === c?.scheme_id && view?.schemeCard?.stages?.[0]?.goal
  const needScheme = c?.scheme_id && !inLog ? c.scheme_id : null
  const [loaded] = useLoad(() => (needScheme ? api.scheme(needScheme) : Promise.resolve(null)), [needScheme])
  if (!c) return null

  const card = inLog ? view.schemeCard : loaded
  const fullStages = card?.stages || stages
  const fraud = Boolean(c.scheme_id)
  const title = view?.scheme?.scheme_name || card?.name || schemeName || (fraud ? c.scheme_id : 'Обычный звонок')
  const reached = c.max_stage || 0
  const conf = view?.scheme ? Math.round(view.scheme.confidence * 100) : null
  const end = view ? callClock(view, view.endedTs ?? view.lastTs) : 0
  const tabs = [['summary', 'Итог'], ...(view ? [['transcript', 'Стенограмма'], ['agent', 'Действия агента']] : [])]
  // Одна фраза могла попасть и в правило, и в требование модели — показываем её один раз с обеими метками.
  const demands = []
  const norm = (q) => q.toLowerCase().replaceAll('ё', 'е').replace(/[\s.!?…,;:]+$/, '')
  for (const w of view?.alarms || []) {
    const labels = w.kind === 'rule' ? w.labels : ['требование']
    for (const h of w.hits) {
      // Правило цитирует предложение, модель — всю реплику: одна и та же фраза, оставляем полную.
      const a = norm(h.quote)
      const same = demands.find((d) => {
        const q = norm(d.quote)
        return q === a || (d.seq != null && d.seq === h.seq && (q.includes(a) || a.includes(q)))
      })
      if (!same) demands.push({ ...h, labels })
      else {
        same.labels = [...new Set([...same.labels, ...labels])]
        if (h.quote.length > same.quote.length) same.quote = h.quote
      }
    }
  }
  demands.sort((a, b) => a.at - b.at)
  const saved = view?.log.find((e) => e.kind === 'tool' && e.tool === 'save_case')
  const n = view?.numberInfo?.calls_count || 0

  function jump(seq) {
    if (seq == null) return
    setTab('transcript')
    setTimeout(() => jumpToLine(seq), 60)
  }
  function copy() {
    const text = reportText({ c, title, fraud, reached, stages: fullStages, demands, hangup: view?.hangup?.text, conf })
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    }, () => {})
  }

  return (
    <div className="report">
      <div className="report-grid">
        <article className="report-main">
          <header className="report-head">
            <div className="pills">
              <span className={`pill ${fraud ? 'pill-red' : 'pill-ok'}`}><span className={`dot ${fraud ? 'dot-red' : 'dot-ok'}`} />{fraud ? 'Мошенничество' : 'Мошенничества не найдено'}</span>
              {replay && <span className="pill">запись</span>}
            </div>
            <h2>{title}</h2>
            {fraud && c.legend && c.legend !== title && <p className="report-legend">Представлялись: {c.legend}</p>}
            <div className="tabs tabs-under" role="tablist">
              {tabs.map(([k, label]) => (
                <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{label}</button>
              ))}
            </div>
          </header>

          {tab === 'summary' && (
            <div className="report-body">
              {view?.lines.length > 0 && (
                <section>
                  <h3>Ход звонка</h3>
                  <div className="report-timeline"><CallTimeline view={view} now={end} onJump={jump} /></div>
                </section>
              )}

              <section>
                <h3>Кратко</h3>
                <p className="prose">{c.summary || 'Агент не написал итог.'}</p>
              </section>

              {fraud && fullStages.length > 0 && (
                <section>
                  <h3>Сценарий мошенника</h3>
                  <p className="section-note">
                    {reached ? `Мошенник дошёл до этапа ${reached} из ${fullStages.length}.` : 'До какого этапа дошёл мошенник, агент не определил.'}
                    {reached && reached < fullStages.length ? ' Ниже — что было бы дальше, если бы разговор продолжился.' : ''}
                  </p>
                  <Scenario stages={fullStages} reached={reached} />
                </section>
              )}

              {demands.length > 0 && (
                <section>
                  <h3>Что требовал мошенник</h3>
                  <ol className="demands">
                    {demands.map((d) => (
                      <li key={d.quote}>
                        <button className="demand" onClick={() => jump(d.seq)} disabled={d.seq == null}>
                          <span className="time">{clock(d.at)}</span>
                          <span><q>{d.quote.replace(/[.…]+$/, '')}</q><span className="demand-label">{d.labels.join(', ')}</span></span>
                        </button>
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              {view?.hints.length > 0 && (
                <section>
                  <h3>Что подсказывал агент</h3>
                  <ol className="hints-list">
                    {view.hints.map((h, k) => (
                      <li key={k}>
                        <span className="time">{clock(h.at)}</span>
                        <div>
                          <p>{h.text}</p>
                          <span className="chip chip-agent">{KINDS[h.kind]}{h.tactic && TACTICS[h.tactic] ? `: ${TACTICS[h.tactic]}` : ''}</span>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              {c.key_phrases?.length > 0 && (
                <section>
                  <h3>Ключевые фразы</h3>
                  <div className="chips">{c.key_phrases.map((p) => <span key={p} className="chip">{p}</span>)}</div>
                </section>
              )}
            </div>
          )}
          {tab === 'transcript' && view && <Transcript lines={view.lines} notes={view.inline} live={false} empty="В этом звонке не было ни одной фразы." />}
          {tab === 'agent' && view && <div className="report-log"><AgentLog log={view.log} open replay={replay} /></div>}
        </article>

        <aside className="report-side">
          <ul className="props">
            <li>
              <span>Номер</span>
              <span>
                {c.phone ? <a href={`#/history/${encodeURIComponent(c.phone)}`}>{fmtPhone(c.phone)}</a> : 'неизвестен'}
                {n > 0 && <span className="prop-sub">звонил уже {n} {plural(n, ['раз', 'раза', 'раз'])}</span>}
              </span>
            </li>
            <li><span>Начало</span>{when(c.started_at)}</li>
            {c.ended_at && <li><span>Длительность</span><span className="mono">{duration(c.started_at, c.ended_at)}</span></li>}
            {sourceLabel(view?.source, replay) && <li><span>Источник</span>{sourceLabel(view?.source, replay)}</li>}
            {fraud && (
              <li>
                <span>Схема</span>
                <span>
                  <a href={`#/schemes/${c.scheme_id}`}>{title}</a>
                  {conf != null && <span className="prop-bar"><span style={{ width: `${conf}%` }} /></span>}
                  {conf != null && <span className="prop-sub">уверенность {conf}%</span>}
                </span>
              </li>
            )}
            {fraud && reached > 0 && <li><span>Этап</span>{reached} из {fullStages.length || '?'}</li>}
            {view && (
              <li>
                <span>Признаков</span>
                <span>
                  {view.alarms.length}
                  {view.alarms[0] && <span className="prop-sub">первый в {clock(view.alarms[0].phraseAt ?? view.alarms[0].at)}</span>}
                </span>
              </li>
            )}
            {view?.scheme?.sinceAlarm != null && (
              <li><span>Агент понял</span><span>через {seconds(view.scheme.sinceAlarm)}<span className="prop-sub">после первого признака{replay ? ', в записи паузы сжаты' : ''}</span></span></li>
            )}
            {view && <li><span>Подсказок</span>{view.hints.length}</li>}
            <li>
              <span>Кейс</span>
              {replay ? 'запись — в базу не пишется' : !view ? 'в базе' : saved?.ok ? 'сохранён в базу' : 'не сохранён'}
            </li>
          </ul>
          <button className="btn btn-wide" onClick={copy}>{copied ? 'Скопировано' : 'Скопировать отчёт'}</button>
          <p className="muted small">Текстом: вывод, требования мошенника и что делать — чтобы переслать близким.</p>
        </aside>
      </div>
    </div>
  )
}
