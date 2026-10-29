// Состояние экрана звонка из событий агента — то же, что call_view в agent/ui_api.py,
// плюс то, что нужно только вебу: пометки на фразах, тайминги и лента действий агента.
// Пересчитывается целиком по списку событий: живой звонок, запись и открытый из истории
// звонок рисуются одним кодом.

const ADVICE = 'Ничего не называй и не переводи.'

/** Разбор текста плашки: правила (agent/rules.py), требования из разбора моделью, история номера. */
export function parseWarning(text) {
  const quotes = [...text.matchAll(/«([^»]+)»/g)].map((m) => m[1])
  let m = /^Признак развода: (.+?) — «/.exec(text)
  if (m) return { kind: 'rule', labels: m[1].split(', '), quotes, text }
  if (text.startsWith('Мошенник требует:')) return { kind: 'demand', labels: ['требование'], quotes, text }
  m = /^С этого номера уже звонили (\d+) раз(?:, схемы: (.+))?\.$/.exec(text)
  if (m) return { kind: 'history', labels: [], quotes: [], count: Number(m[1]), schemes: m[2] ? m[2].split(', ') : [], text }
  return { kind: 'other', labels: [], quotes, text: text.replace(ADVICE, '').trim() }
}

const norm = (s) => s.toLowerCase().replaceAll('ё', 'е')
// Как tokenize в rag/chunks.py: слово, обрезанное до 5 букв, — грубый стеммер для сверки цитат.
const stems = (s) => (norm(s).match(/[a-zа-я0-9]+/g) || []).map((w) => w.slice(0, 5))
const QUOTE_OVERLAP = 0.7 // как в agent/policy.py: цитата модели может быть пересказом

/** Где в разговоре сказана цитата: точное место в реплике или вся реплика, если модель пересказала. */
export function findQuote(lines, quote) {
  const q = norm(quote).replace(/[\s.!?…,;:]+$/, '').trim()
  if (!q) return null
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]
    if (line.speaker === 'user') continue
    const at = norm(line.text).indexOf(q)
    if (at >= 0) return { line, start: at, end: at + q.length }
  }
  const want = stems(quote)
  let best = null
  for (const line of lines) {
    if (line.speaker === 'user') continue
    const said = new Set(stems(line.text))
    const score = want.filter((t) => said.has(t)).length / want.length
    if (score >= QUOTE_OVERLAP && (!best || score > best.score)) best = { line, start: 0, end: line.text.length, score }
  }
  return best
}

/**
 * Время разговора в момент ts, секунды. Считается от последней реплики (её t_end — время в записи
 * звонка), а не от первого события: при проигрывании с ускорением настенные часы идут быстрее
 * разговора, и время плашки разошлось бы со временем фразы в стенограмме.
 */
export function callClock(v, ts) {
  if (v.startedTs === null) return 0
  if (!v.lastLine) return Math.max(0, (ts - v.startedTs) / 1000)
  return v.lastLine.tEnd + Math.max(0, (ts - v.lastLine.ts) / 1000)
}

export function callView(events) {
  const v = {
    callId: null,
    phone: null,
    source: null,
    startedTs: null,
    endedTs: null,
    lastTs: null,
    lastLine: null, // последняя реплика: от неё считается время разговора
    lines: [],
    scheme: null, // последняя объявленная схема (+ ts, at)
    schemeCard: null, // схема целиком из get_scheme: легенда, этапы с next_move, тактики
    stages: [],
    stage: null,
    maxStage: 0,
    warnings: [],
    suggestions: [],
    log: [], // лента действий агента
    inline: [], // события агента между репликами: где в разговоре он понял схему и сменился этап
    numberInfo: null,
    summary: null,
    ended: false,
  }
  const openTools = []
  for (const e of events) {
    const p = e.payload
    const ts = Date.parse(e.ts)
    v.callId = e.call_id
    if (v.startedTs === null) v.startedTs = ts
    v.lastTs = ts
    const at = callClock(v, ts)
    switch (e.type) {
      case 'call_started':
        v.phone = p.phone
        v.source = p.source
        break
      case 'transcript':
        v.lines.push({ seq: p.seq, speaker: p.speaker, text: p.text, tStart: p.t_start, tEnd: p.t_end, ts, at, marks: [] })
        v.lastLine = v.lines.at(-1)
        break
      case 'warning': {
        const w = { ...parseWarning(p.text), ts, at, hits: [] }
        for (const quote of w.quotes) {
          const hit = findQuote(v.lines, quote)
          w.hits.push({ quote, seq: hit?.line.seq ?? null, at: hit?.line.tStart ?? at })
          if (!hit) continue
          hit.line.marks.push({ start: hit.start, end: hit.end, kind: w.kind, labels: w.labels })
          // Сколько прошло от последней процитированной фразы до плашки: у правил — доли секунды.
          const lag = Math.max(0, (ts - hit.line.ts) / 1000)
          if (w.lag == null || lag < w.lag) [w.lag, w.lineSeq, w.phraseAt] = [lag, hit.line.seq, hit.line.tStart]
        }
        v.warnings.push(w)
        v.log.push({ kind: 'warning', at, w })
        break
      }
      case 'scheme_detected': {
        const firstAlarm = v.warnings.find((w) => w.kind !== 'history')
        v.scheme = { ...p, ts, at, sinceAlarm: firstAlarm ? at - firstAlarm.at : null }
        v.stages = p.stages?.length ? p.stages : v.schemeCard?.id === p.scheme_id ? (v.schemeCard.stages ?? []) : []
        v.stage = null
        for (const quote of p.evidence) {
          const hit = findQuote(v.lines, quote)
          if (hit) hit.line.marks.push({ start: hit.start, end: hit.end, kind: 'evidence', labels: [] })
        }
        v.log.push({ kind: 'scheme', at, scheme: v.scheme })
        v.inline.push({ kind: 'scheme', afterSeq: v.lastLine?.seq ?? -1, at, scheme: v.scheme })
        break
      }
      case 'stage_changed':
        v.stage = p
        v.maxStage = Math.max(v.maxStage, p.stage_n)
        v.log.push({ kind: 'stage', at, stage: p })
        // Этап сразу после объявления схемы уже виден в её строке — отдельной не нужно.
        if (v.inline.at(-1)?.kind !== 'scheme' || v.inline.at(-1).afterSeq !== (v.lastLine?.seq ?? -1))
          v.inline.push({ kind: 'stage', afterSeq: v.lastLine?.seq ?? -1, at, stage: p })
        else v.inline.at(-1).stage = p
        break
      case 'suggestion':
        v.suggestions.push({ ...p, ts, at })
        v.log.push({ kind: 'suggestion', at, suggestion: p })
        break
      case 'tool_call':
        openTools.push({ tool: p.tool, args: p.args, ts })
        break
      case 'tool_result': {
        const i = openTools.findIndex((t) => t.tool === p.tool)
        const call = i >= 0 ? openTools.splice(i, 1)[0] : null
        v.log.push({ kind: 'tool', at, tool: p.tool, ok: p.ok, ms: call ? ts - call.ts : null, args: call?.args, result: p.result })
        if (p.tool === 'lookup_number' && p.ok) v.numberInfo = p.result
        if (p.tool === 'get_scheme' && p.ok && p.result) v.schemeCard = p.result
        break
      }
      case 'summary':
        v.summary = p
        v.log.push({ kind: 'summary', at })
        break
      case 'call_ended':
        v.ended = true
        v.endedTs = ts
        v.log.push({ kind: 'ended', at })
        break
    }
  }
  // Лог записан до 30.10: этапов в scheme_detected нет — берём их из карточки схемы.
  if (v.scheme && !v.stages.length && v.schemeCard?.id === v.scheme.scheme_id) v.stages = v.schemeCard.stages ?? []
  v.verdict = v.scheme ? 'scheme' : v.warnings.length ? 'warning' : 'none'
  v.hangup = v.suggestions.findLast((s) => s.kind === 'hangup') ?? null
  v.hints = v.suggestions.filter((s) => s.kind !== 'hangup')
  v.alarms = v.warnings.filter((w) => w.kind !== 'history')
  return v
}

/** Отрезки реплики для подсветки: пометки могут пересекаться, красная важнее основания схемы. */
export function segments(text, marks) {
  if (!marks.length) return [{ text, kind: null, labels: [] }]
  const cuts = [...new Set([0, text.length, ...marks.flatMap((m) => [m.start, m.end])])].sort((a, b) => a - b)
  const rank = { rule: 3, demand: 3, other: 2, evidence: 1 }
  const out = []
  for (let i = 0; i < cuts.length - 1; i++) {
    const [a, b] = [cuts[i], cuts[i + 1]]
    if (a === b) continue
    const over = marks.filter((m) => m.start <= a && m.end >= b)
    const top = over.sort((x, y) => rank[y.kind] - rank[x.kind])[0]
    const prev = out.at(-1)
    if (prev && prev.kind === (top?.kind ?? null)) prev.text += text.slice(a, b)
    else out.push({ text: text.slice(a, b), kind: top?.kind ?? null })
  }
  return out
}
