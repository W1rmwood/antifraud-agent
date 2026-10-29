import { useEffect, useLayoutEffect, useRef } from 'react'
import { segments } from '../callView.js'
import { clock, SPEAKERS } from '../format.js'
import { Alert, Steps, Target } from '../icons.jsx'

/** Строка агента в ленте: в этот момент разговора он понял схему или увидел новый этап. */
function AgentNote({ note }) {
  if (note.kind === 'scheme')
    return (
      <li className="tnote tnote-agent">
        <span className="tline-time">{clock(note.at)}</span>
        <span className="tnote-body">
          <Target size={14} />
          <span>
            Агент определил схему <b>{note.scheme.scheme_name}</b>, {Math.round(note.scheme.confidence * 100)}%
            {note.stage && <span className="muted">, этап {note.stage.stage_n}: {note.stage.stage_name}</span>}
          </span>
        </span>
      </li>
    )
  return (
    <li className="tnote">
      <span className="tline-time">{clock(note.at)}</span>
      <span className="tnote-body"><Steps size={14} /><span>Этап {note.stage.stage_n}: {note.stage.stage_name}</span></span>
    </li>
  )
}

/** Пока не прозвучало ни фразы: показать, что агент жив и слушает. */
export function Listening({ text }) {
  return (
    <div className="listening">
      <span className="bars" aria-hidden="true"><i /><i /><i /><i /></span>
      {text}
    </div>
  )
}

const AVATAR = { caller: 'З', user: 'В', unknown: 'Г' }
const MARK_TITLE = {
  rule: 'Сработало правило: признак развода',
  demand: 'Модель нашла требование мошенника',
  other: 'Плашка агента',
  evidence: 'На этой фразе модель определила схему',
}

/** Лента реплик. Фразы, на которые сработали правила и модель, подсвечены, рядом — что за признак. */
export default function Transcript({ lines, live, empty, notes = [] }) {
  const box = useRef(null)
  const stick = useRef(true) // прокручивать вниз, пока человек сам не отлистал наверх

  useEffect(() => {
    const el = box.current
    const onScroll = () => (stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80)
    el.addEventListener('scroll', onScroll)
    return () => el.removeEventListener('scroll', onScroll)
  }, [])
  useLayoutEffect(() => {
    if (live && stick.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines, live])

  return (
    <div className="transcript" ref={box}>
      {lines.length === 0 && (live ? <Listening text={empty} /> : <div className="empty">{empty}</div>)}
      <ol className="tlines">
        {lines.map((line, i) => {
          const head = i === 0 || lines[i - 1].speaker !== line.speaker
          const labels = [...new Set(line.marks.filter((m) => m.kind !== 'evidence').flatMap((m) => m.labels))]
          const after = notes.filter((n) => n.afterSeq === line.seq)
          return [
            <li key={line.seq} id={`line-${line.seq}`} className={`tline ${head ? 'tline-head' : ''}`} data-speaker={line.speaker}>
              <span className="tline-time">{clock(line.tStart)}</span>
              <div className="tline-body">
                {head && (
                  <div className="tline-who">
                    <span className={`avatar avatar-${line.speaker}`}>{AVATAR[line.speaker]}</span>
                    {SPEAKERS[line.speaker]}
                  </div>
                )}
                <p className="tline-text">
                  {segments(line.text, line.marks).map((s, k) =>
                    s.kind ? <mark key={k} className={`mark-${s.kind}`} title={MARK_TITLE[s.kind]}>{s.text}</mark> : s.text,
                  )}
                </p>
                {labels.length > 0 && (
                  <div className="tline-flags">
                    {labels.map((l) => (
                      <span key={l} className="flag"><Alert size={12} />{l}</span>
                    ))}
                  </div>
                )}
              </div>
            </li>,
            ...after.map((n, k) => <AgentNote key={`n${line.seq}-${k}`} note={n} />),
          ]
        })}
      </ol>
    </div>
  )
}
