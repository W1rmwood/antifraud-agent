import { clock } from '../format.js'

const ROWS = [
  ['caller', 'Звонящий'],
  ['user', 'Вы'],
]

/** Прокрутить стенограмму к реплике и коротко подсветить её. */
export function jumpToLine(seq) {
  const el = document.getElementById(`line-${seq}`)
  if (!el) return
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  el.classList.remove('tline-flash')
  void el.offsetWidth // перезапуск анимации, если по той же фразе кликнули ещё раз
  el.classList.add('tline-flash')
}

/**
 * Полоса звонка: кто когда говорил (по t_start / t_end реплик), где сработали признаки
 * и когда агент понял схему. Шкала растёт вместе со звонком.
 */
export default function CallTimeline({ view, now, onJump = jumpToLine }) {
  const { lines, alarms, scheme } = view
  const end = Math.max(now, lines.at(-1)?.tEnd ?? 0, 30)
  const pct = (t) => `${Math.min(100, (t / end) * 100)}%`
  // Живой звук — один канал, роли не разделены: одна строка «Голос».
  const mono = lines.some((l) => l.speaker === 'unknown')
  const rows = mono ? [['unknown', 'Голос']] : ROWS

  return (
    <div className="timeline" aria-label="Ход звонка">
      <div className="tl-labels">
        {rows.map(([k, label]) => <span key={k}>{label}</span>)}
      </div>
      <div className="tl-track">
        {rows.map(([k]) => (
          <div key={k} className={`tl-row tl-${k}`}>
            {lines.filter((l) => l.speaker === k).map((l) => {
              const flagged = l.marks.some((m) => m.kind !== 'evidence')
              return (
                <button key={l.seq} className={`tl-seg ${flagged ? 'tl-seg-red' : ''}`} onClick={() => onJump(l.seq)}
                  style={{ left: pct(l.tStart), width: `max(3px, calc(${pct(l.tEnd - l.tStart)} - 2px))` }}
                  title={`${clock(l.tStart)} ${l.text}`} aria-label={`Реплика в ${clock(l.tStart)}`} />
              )
            })}
          </div>
        ))}
        {alarms.map((w, k) => (
          <button key={k} className="tl-mark tl-mark-red" style={{ left: pct(w.phraseAt ?? w.at) }}
            onClick={() => w.lineSeq != null && onJump(w.lineSeq)}
            title={`${clock(w.phraseAt ?? w.at)} ${w.kind === 'rule' ? w.labels.join(', ') : 'требование'}`}
            aria-label={`Признак в ${clock(w.phraseAt ?? w.at)}`} />
        ))}
        {scheme && (
          <span className="tl-mark tl-mark-agent" style={{ left: pct(scheme.at) }}
            title={`${clock(scheme.at)} агент определил схему: ${scheme.scheme_name}`} />
        )}
        <span className="tl-now" style={{ left: pct(now) }} />
      </div>
      <div className="tl-scale">
        <span>00:00</span>
        <span>{clock(end)}</span>
      </div>
    </div>
  )
}
