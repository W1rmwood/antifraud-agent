// Мост ui/server.py: запуск и остановка звонка, поток событий, справочники.
import { useEffect, useState } from 'react'

async function json(url, init) {
  const res = await fetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.error || `Сервер ответил ${res.status}`)
  return body
}

const post = (url, body) =>
  json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })

export const api = {
  sources: () => json('/api/sources'),
  start: (body) => post('/api/calls', body),
  stop: (callId) => post(`/api/calls/${callId}/stop`),
  schemes: () => json('/api/schemes'),
  scheme: (id) => json(`/api/schemes/${id}`),
  cases: (limit = 100) => json(`/api/cases?limit=${limit}`),
  runs: () => json('/api/runs'),
  health: () => json('/api/health'),
  number: (phone) => json(`/api/numbers/${encodeURIComponent(phone)}`),
}

/**
 * Кейсы из базы и звонки этого компьютера из data/runs/ одним списком.
 * Запись (проигрывание) кейс в базу не сохраняет, поэтому без логов её не было бы в истории.
 */
export function mergeHistory(cases, runs) {
  const fromRuns = runs.map((r) =>
    r.summary
      ? { ...r.summary, call_id: r.call_id, started_at: r.started_at, replay: r.replay }
      : { call_id: r.call_id, phone: r.phone, started_at: r.started_at, scheme_id: null, noReport: true, ended: r.ended, running: r.running },
  )
  const seen = new Set(fromRuns.map((c) => c.call_id))
  return [...fromRuns, ...cases.filter((c) => !seen.has(c.call_id))].sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))
}

/**
 * События звонка из лога по мере записи (контракт №3) и состояние процесса.
 * missing — лога нет: звонок не найден или ещё не создан.
 */
export function useCallStream(callId) {
  const [state, setState] = useState({ callId, events: [], process: null, done: false, missing: false })
  useEffect(() => {
    if (!callId) return
    setState({ callId, events: [], process: null, done: false, missing: false })
    const es = new EventSource(`/api/calls/${callId}/events`)
    // Записанный звонок приходит сотней событий разом — копим их и рисуем один раз за кадр.
    let pending = []
    let frame = 0
    const flush = () => {
      frame = 0
      const add = pending
      pending = []
      if (add.length) setState((s) => ({ ...s, events: s.events.concat(add) }))
    }
    es.addEventListener('agent', (m) => {
      pending.push(JSON.parse(m.data))
      if (!frame) frame = requestAnimationFrame(flush)
    })
    es.addEventListener('process', (m) => setState((s) => ({ ...s, process: JSON.parse(m.data) })))
    es.addEventListener('end', () => {
      es.close()
      cancelAnimationFrame(frame)
      flush()
      setState((s) => ({ ...s, done: true }))
    })
    // 404 и 400 браузер не переподключает: соединение закрыто — лога нет.
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) setState((s) => ({ ...s, missing: s.events.length === 0, done: true }))
    }
    return () => {
      es.close()
      cancelAnimationFrame(frame)
    }
  }, [callId])
  return state.callId === callId ? state : { callId, events: [], process: null, done: false, missing: false }
}

/** Данные справочника, загруженные один раз: [data, error]. */
export function useLoad(load, deps) {
  const [state, setState] = useState([null, null])
  useEffect(() => {
    let alive = true
    setState([null, null])
    load().then(
      (data) => alive && setState([data, null]),
      (err) => alive && setState([null, err]),
    )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}
