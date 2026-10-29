import { useEffect, useMemo, useState } from 'react'
import { api, useCallStream, useLoad } from '../api.js'
import { callClock, callView } from '../callView.js'
import { AgentLog, Hint, Scheme, Signs, Verdict } from '../components/AgentPanel.jsx'
import CallTimeline from '../components/CallTimeline.jsx'
import Transcript from '../components/Transcript.jsx'
import { clock, phone as fmtPhone, plural } from '../format.js'
import { Alert, File, Mic, Play, Speaker, Stop } from '../icons.jsx'
import { diagnose } from '../diagnose.js'
import Report from './Report.jsx'

function useNow(active) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

function SourceChip({ source, replay }) {
  if (replay) return <span className="chip"><Play size={12} />запись, без модели</span>
  if (!source) return null
  const Icon = { file: File, mic: Mic, loopback: Speaker }[source.kind] || File
  const label = source.kind === 'file' ? source.path.split('/').pop() : source.kind === 'mic' ? 'микрофон' : 'звук компьютера'
  return <span className="chip"><Icon size={12} />{label}</span>
}

/** Экран звонка: идёт сейчас, закончился или открыт из истории — одни и те же события. */
export default function CallPage({ callId, showProvoke }) {
  const stream = useCallStream(callId)
  const view = useMemo(() => callView(stream.events), [stream.events])
  const [tab, setTab] = useState('call')
  const [stopping, setStopping] = useState(false)
  const [stopError, setStopError] = useState(null)

  // Лог записан до 30.10: этапов в событии нет — догружаем карточку схемы.
  const needCard = view.scheme && !view.stages.length ? view.scheme.scheme_id : null
  const [card] = useLoad(() => (needCard ? api.scheme(needCard) : Promise.resolve(null)), [needCard])
  const stages = view.stages.length ? view.stages : card?.stages || []

  const proc = stream.process
  const running = proc ? proc.running : !stream.done
  const live = running && !view.ended
  const now = useNow(live)
  const elapsed = callClock(view, view.endedTs ?? (live ? now : view.lastTs))
  const crashed = proc && !proc.running && proc.returncode !== 0 && !view.ended
  const isStopping = stopping || proc?.stopping

  useEffect(() => {
    if (view.ended || crashed) dispatchEvent(new Event('antifraud:busy'))
  }, [view.ended, crashed])

  useEffect(() => {
    setTab('call')
    setStopping(false)
    setStopError(null)
  }, [callId])

  async function stop() {
    setStopping(true)
    try {
      await api.stop(callId)
    } catch (err) {
      setStopError(err.message)
      setStopping(false)
    }
  }

  if (stream.missing)
    return (
      <>
        <header className="topbar"><h1>Звонок</h1></header>
        <div className="empty-page">
          <p>Звонка <code>{callId}</code> нет: лог <code>data/runs/{callId}.jsonl</code> не найден.</p>
          <a className="btn" href="#/new">Начать новый звонок</a>
        </div>
      </>
    )

  const n = view.numberInfo?.calls_count || 0
  const replay = proc?.source === 'replay' || Boolean(view.summary && view.summary.call_id !== view.callId)
  const deadOnStart = crashed && view.lines.length === 0
  return (
    <>
      <header className="topbar">
        <nav className="crumbs">
          <span>Звонок</span>
          <span className="crumb-sep">/</span>
          <h1>{view.phone ? fmtPhone(view.phone) : 'Номер неизвестен'}</h1>
        </nav>
        {n > 0 && <span className="chip chip-red">звонил уже {n} {plural(n, ['раз', 'раза', 'раз'])}</span>}
        <SourceChip source={view.source} replay={replay} />
        {!deadOnStart && (
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'call'} className={tab === 'call' ? 'on' : ''} onClick={() => setTab('call')}>Разговор</button>
            <button role="tab" aria-selected={tab === 'report'} className={tab === 'report' ? 'on' : ''} onClick={() => setTab('report')} disabled={!view.summary}>
              Отчёт
            </button>
          </div>
        )}
        <div className="topbar-end">
          <span className={`status ${live ? 'status-live' : ''}`}>
            {live && <span className="dot dot-live" />}
            {live ? (isStopping ? 'Завершаю' : 'Идёт') : view.ended ? 'Закончен' : crashed ? 'Агент упал' : 'Загрузка'}
            {!deadOnStart && <span className="mono">{clock(elapsed)}</span>}
          </span>
          {live && proc && (
            <button className="btn btn-danger" onClick={stop} disabled={isStopping}>
              <Stop size={14} />{isStopping ? 'Агент дописывает отчёт…' : 'Закончить'}
            </button>
          )}
        </div>
      </header>

      {stopError && <p className="banner banner-red">{stopError}</p>}
      {stream.done && !view.ended && !crashed && view.lines.length > 0 && (
        <p className="banner">Лог обрывается: звонок не закончился штатно, отчёта нет.</p>
      )}
      {view.summary && tab === 'call' && !live && (
        <p className="banner banner-ok">
          {replay ? 'Запись закончилась, отчёт — из записанного звонка.' : 'Звонок закончен, кейс сохранён в базу.'}
          <button className="link" onClick={() => setTab('report')}>Открыть отчёт</button>
        </p>
      )}

      {deadOnStart ? (
        <div className="crash-page"><Crash error={proc.error} started={false} /></div>
      ) : tab === 'report' && view.summary ? (
        <Report view={view} stages={stages} replay={replay} />
      ) : (
        <div className="call">
          {view.lines.length > 0 && <CallTimeline view={view} now={elapsed} />}
          <Transcript
            lines={view.lines}
            notes={view.inline}
            live={live}
            empty={live ? 'Ждём первую фразу. Агент слушает звонок.' : 'В этом звонке не было ни одной фразы.'}
          />
          <aside className="agent" aria-label="Подсказки агента">
            {crashed && <Crash error={proc.error} started compact />}
            <Verdict view={view} live={live} />
            <Hint hints={view.hints} showProvoke={showProvoke} />
            <Scheme view={view} stages={stages} />
            <Signs warnings={view.warnings} />
            <AgentLog log={view.log} replay={replay} />
          </aside>
        </div>
      )}
    </>
  )
}

/** Агент упал: причина человеческими словами, одна команда и выход — показать запись без модели. */
function Crash({ error, started, compact = false }) {
  const { reason, fix } = diagnose(error)
  const [copied, setCopied] = useState(false)
  const [starting, setStarting] = useState(false)
  async function replayDemo() {
    setStarting(true)
    try {
      const call = await api.start({ source: 'replay', path: 'data/runs/demo.jsonl', speed: 1 })
      dispatchEvent(new Event('antifraud:busy'))
      location.hash = `#/call/${call.call_id}`
    } catch {
      setStarting(false)
    }
  }
  function copy() {
    navigator.clipboard?.writeText(fix).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }, () => {})
  }
  return (
    <section className={`crash ${compact ? 'crash-compact' : ''}`}>
      <header className="crash-head"><Alert />{started ? 'Агент упал посреди звонка' : 'Агент не запустился'}</header>
      <p className="crash-reason">{reason}</p>
      {fix && (
        <div className="crash-fix">
          <span className="muted">Что сделать</span>
          <code>{fix}</code>
          <button className="btn" onClick={copy}>{copied ? 'Скопировано' : 'Скопировать'}</button>
        </div>
      )}
      {!compact && (
        <>
          <p className="muted">Отчёт не сохранён. Пока модель не готова, показать работу интерфейса можно на записи — она идёт без модели.</p>
          <div className="crash-actions">
            <button className="btn btn-primary" onClick={replayDemo} disabled={starting}><Play size={14} />{starting ? 'Запускаю…' : 'Запустить демо-запись'}</button>
            <a className="btn" href="#/new/demo">Все записи без модели</a>
          </div>
        </>
      )}
      {error && (
        <details className="crash-trace">
          <summary>Текст ошибки</summary>
          <pre>{error}</pre>
        </details>
      )}
    </section>
  )
}
