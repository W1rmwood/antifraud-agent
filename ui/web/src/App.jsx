import { useCallback, useEffect, useState } from 'react'
import { api, mergeHistory, useLoad } from './api.js'
import Sidebar from './components/Sidebar.jsx'
import CallPage from './screens/CallPage.jsx'
import { CasePage, History, NumberPage } from './screens/History.jsx'
import NewCall from './screens/NewCall.jsx'
import { SchemeDetail, Schemes } from './screens/Schemes.jsx'

function useRoute() {
  const read = () => location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const on = () => setRoute(read())
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  return route
}

function useStored(key, fallback) {
  const [value, setValue] = useState(() => {
    try {
      const v = localStorage.getItem(key)
      return v === null ? fallback : JSON.parse(v)
    } catch {
      return fallback
    }
  })
  const set = useCallback((v) => {
    setValue(v)
    try {
      localStorage.setItem(key, JSON.stringify(v))
    } catch { /* приватное окно — настройка живёт до перезагрузки */ }
  }, [key])
  return [value, set]
}

/**
 * Звонки этого компьютера (data/runs/) — для левой панели. Опрос раз в пару секунд
 * и сразу по событию «звонок начался / закончился».
 */
function useRuns() {
  const [runs, setRuns] = useState([])
  useEffect(() => {
    let alive = true
    const check = () => api.runs().then((r) => alive && setRuns(r), () => {})
    check()
    const t = setInterval(check, 2500)
    addEventListener('antifraud:busy', check)
    return () => {
      alive = false
      clearInterval(t)
      removeEventListener('antifraud:busy', check)
    }
  }, [])
  return runs
}

export default function App() {
  const route = useRoute()
  const runs = useRuns()
  const [health] = useLoad(api.health, [route[0]])
  const [schemes] = useLoad(api.schemes, [])
  const [cases] = useLoad(() => api.cases(500).catch(() => []), [route[0]])
  const [showProvoke, setShowProvoke] = useStored('showProvoke', true)
  const [page, arg] = route
  const busy = runs.find((r) => r.running)?.call_id ?? null

  let screen
  if (page === 'call' && arg) screen = <CallPage callId={arg} showProvoke={showProvoke} />
  else if (page === 'history' && arg) screen = <NumberPage phone={arg} />
  else if (page === 'history') screen = <History />
  else if (page === 'case' && arg) screen = <CasePage callId={arg} />
  else if (page === 'schemes' && arg) screen = <SchemeDetail id={arg} />
  else if (page === 'schemes') screen = <Schemes />
  else
    screen = (
      <NewCall tab={page === 'new' ? arg : undefined} busy={busy} health={health} showProvoke={showProvoke} setShowProvoke={setShowProvoke}
        onStarted={(id) => { dispatchEvent(new Event('antifraud:busy')); location.hash = `#/call/${id}` }} />
    )

  return (
    <div className="app">
      <Sidebar route={route} runs={runs} health={health}
        historyCount={cases ? mergeHistory(cases, runs).length : null} schemesCount={schemes?.length ?? null} />
      <main className="main">{screen}</main>
    </div>
  )
}
