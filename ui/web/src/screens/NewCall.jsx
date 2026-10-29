import { useMemo, useRef, useState } from 'react'
import { api, useLoad } from '../api.js'
import { phone as fmtPhone } from '../format.js'
import { File, Mic, Play, Speaker, Upload } from '../icons.jsx'

const SPEEDS = [1, 2, 4]
const TABS = [
  ['live', 'Слушать вживую', Mic],
  ['upload', 'Загрузить запись', Upload],
  ['tests', 'Тестовые звонки', File],
  ['demo', 'Демо без модели', Play],
]
const ACCEPT = '.wav,.flac,.ogg,.mp3,.txt'

/** Чего не хватает для звонка через модель: записи — ничего, аудиофайлам из репозитория — ещё и Git LFS,
 *  звуку (файл или микрофон) — модель распознавания речи. */
function blockers(health, { lfs = false, asr = lfs } = {}) {
  if (!health) return []
  const need = ['deps', 'llm', 'embed', 'index', ...(lfs ? ['audio'] : []), ...(asr ? ['asr'] : [])]
  return health.checks.filter((c) => need.includes(c.key) && !c.ok)
}

function Need({ missing }) {
  if (!missing.length) return null
  return (
    <div className="need">
      <p>Агенту нужна модель, а на этом компьютере не хватает:</p>
      <ul>
        {missing.map((c) => (
          <li key={c.key}><span className="dot dot-orange" /><span>{c.label}: {c.detail}</span><code>{c.fix}</code></li>
        ))}
      </ul>
      <p className="muted small">Пока модели нет, работу интерфейса можно показать на вкладке <a href="#/new/demo">«Демо без модели»</a>.</p>
    </div>
  )
}

function PhoneField({ value, onChange }) {
  return (
    <label className="field-col">
      <span>Номер звонящего <small>необязательно</small></span>
      <input className="input mono" value={value} onChange={(e) => onChange(e.target.value)} placeholder="+7 999 000-00-02" inputMode="tel" autoComplete="off" />
      <small>Агент сразу проверит, звонил ли этот номер раньше и с какой схемой.</small>
    </label>
  )
}

function ProvokeToggle({ showProvoke, setShowProvoke }) {
  return (
    <label className="field toggle">
      <span>
        Грубые подсказки
        <small>Режим провокации: абсурд, издёвка, бывает мат</small>
      </span>
      <input type="checkbox" checked={showProvoke} onChange={(e) => setShowProvoke(e.target.checked)} />
    </label>
  )
}

function useStart(onStarted) {
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState(null)
  async function start(body) {
    setStarting(true)
    setError(null)
    try {
      const call = await api.start(body)
      onStarted(call.call_id)
    } catch (err) {
      setError(err.message)
      setStarting(false)
    }
  }
  return { start, starting, error }
}

function Busy({ busy }) {
  return <p className="notice">Уже идёт звонок: модель одна, второй придётся подождать. <a href={`#/call/${busy}`}>Открыть текущий</a></p>
}

/** Вкладка «Слушать вживую»: микрофон на громкой связи или звук компьютера. */
function Live({ health, busy, onStarted, showProvoke, setShowProvoke }) {
  const [source, setSource] = useState('mic')
  const [phone, setPhone] = useState('')
  const { start, starting, error } = useStart(onStarted)
  const missing = blockers(health, { asr: true })
  return (
    <div className="newcall">
      <div className="mode-main">
        <div className="mode-head">
          <h2>Агент слушает разговор и подсказывает по ходу</h2>
          <p className="muted">Включите громкую связь или звоните с этого ноутбука. Выберите, откуда агенту слушать.</p>
        </div>
        <div className="choice" role="radiogroup" aria-label="Откуда слушать">
          {[
            ['mic', Mic, 'Микрофон', 'Телефон на громкой связи рядом с ноутбуком. macOS спросит разрешение при первом запуске — для терминала, в котором запущен агент.'],
            ['loopback', Speaker, 'Звук компьютера', 'Звонок в мессенджере на этом ноутбуке. Агент слышит обе стороны разговора.'],
          ].map(([k, Icon, title, note]) => (
            <button key={k} role="radio" aria-checked={source === k} className={`choice-card ${source === k ? 'on' : ''}`} onClick={() => setSource(k)}>
              <span className="choice-icon"><Icon size={22} /></span>
              <span className="choice-title">{title}</span>
              <span className="choice-note">{note}</span>
              <span className="choice-pick">{source === k ? 'Выбрано' : 'Выбрать'}</span>
            </button>
          ))}
        </div>
        <section className="flow">
          <h3>Как пойдёт звонок</h3>
          <ol>
            <li><span className="flow-n">1</span><b>Фраза — на экране</b><span>Агент распознаёт речь и показывает реплику примерно через 2 с после того, как её договорили.</span></li>
            <li><span className="flow-n">2</span><b>Признак — плашка</b><span>Правила без модели ставят красную плашку сразу за фразой: перевод, код из СМС, наличные, «никому не говорите».</span></li>
            <li><span className="flow-n">3</span><b>Схема — подсказка</b><span>Модель узнаёт схему обычно через 10–20 с после первого требования: этап, что будет дальше и что сказать.</span></li>
          </ol>
        </section>
      </div>
      <aside className="newcall-side">
        <div className="side-card">
          <p className="side-label">Звонок</p>
          <PhoneField value={phone} onChange={setPhone} />
          <ProvokeToggle showProvoke={showProvoke} setShowProvoke={setShowProvoke} />
          <Need missing={missing} />
          {busy ? <Busy busy={busy} /> : (
            <button className="btn btn-primary btn-big" disabled={starting || missing.length > 0} onClick={() => start({ source, phone })}>
              <span className="rec" />
              {starting ? 'Запускаю агента…' : 'Начать слушать'}
            </button>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      </aside>
    </div>
  )
}

/** Вкладка «Загрузить запись»: файл звонка или расшифровка — агент разберёт её как звонок. */
function UploadMode({ health, busy, onStarted, showProvoke, setShowProvoke }) {
  const [file, setFile] = useState(null)
  const [uploaded, setUploaded] = useState(null)
  const [progress, setProgress] = useState(null)
  const [uploadError, setUploadError] = useState(null)
  const [over, setOver] = useState(false)
  const [phone, setPhone] = useState('')
  const [realtime, setRealtime] = useState(true)
  const input = useRef(null)
  const { start, starting, error } = useStart(onStarted)
  const missing = blockers(health)

  function pick(f) {
    if (!f) return
    setFile(f)
    setUploaded(null)
    setUploadError(null)
    setProgress(0)
    // XHR, а не fetch: нужен прогресс загрузки — записи звонков бывают по сотне мегабайт.
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/upload?name=${encodeURIComponent(f.name)}`)
    xhr.upload.onprogress = (e) => e.lengthComputable && setProgress(e.loaded / e.total)
    xhr.onload = () => {
      let body = {}
      try {
        body = JSON.parse(xhr.responseText || '{}')
      } catch { /* ответ не JSON — покажем код */ }
      if (xhr.status === 201) setUploaded(body)
      else setUploadError(body.error || `Сервер ответил ${xhr.status}`)
      setProgress(null)
    }
    xhr.onerror = () => {
      setUploadError('Не удалось отправить файл: мост не отвечает')
      setProgress(null)
    }
    xhr.send(f)
  }

  const size = file ? (file.size > 1048576 ? `${(file.size / 1048576).toFixed(1).replace('.', ',')} МБ` : `${Math.ceil(file.size / 1024)} КБ`) : ''
  return (
    <div className="newcall">
      <div className="mode-main">
        <div className="mode-head">
          <h2>Разобрать запись звонка</h2>
          <p className="muted">Агент прослушает файл как живой звонок: найдёт признаки развода, схему и этап и сохранит отчёт.</p>
        </div>
        <div
          className={`drop ${over ? 'drop-over' : ''} ${file ? 'drop-has' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true) }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]) }}
          onClick={() => input.current?.click()}
          role="button" tabIndex={0} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
        >
          <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => pick(e.target.files[0])} />
          {file ? (
            <>
              <span className="drop-icon"><File size={26} /></span>
              <span className="drop-title">{file.name}</span>
              <span className="drop-note mono">{size}{progress != null ? `, загружаю ${Math.round(progress * 100)}%` : uploaded ? ', загружено' : ''}</span>
              {progress != null && <span className="drop-bar"><span style={{ width: `${progress * 100}%` }} /></span>}
              <span className="link">Выбрать другой файл</span>
            </>
          ) : (
            <>
              <span className="drop-icon"><Upload size={26} /></span>
              <span className="drop-title">Перетащите запись звонка сюда</span>
              <span className="drop-note">или нажмите, чтобы выбрать файл</span>
            </>
          )}
        </div>
        {uploadError && <p className="error">{uploadError}</p>}
        <div className="formats">
          <div><b>Аудио</b><span>WAV, FLAC, OGG, MP3. В стерео левый канал — звонящий, правый — вы; в моно роли не разделяются.</span></div>
          <div><b>Расшифровка</b><span>.txt, по реплике на строку: «caller: …» — звонящий, «user: …» — вы. Распознавание речи не нужно.</span></div>
        </div>
      </div>
      <aside className="newcall-side">
        <div className="side-card">
          <p className="side-label">Разбор</p>
          <PhoneField value={phone} onChange={setPhone} />
          <div className="field field-stack">
            <span>Темп разбора<small>{realtime ? 'Реплики идут с паузами, как в настоящем звонке — для показа' : 'Агент разбирает запись так быстро, как успевает модель'}</small></span>
            <div className="segmented" role="radiogroup" aria-label="Темп разбора">
              <button role="radio" aria-checked={realtime} className={realtime ? 'on' : ''} onClick={() => setRealtime(true)}>Как звонок</button>
              <button role="radio" aria-checked={!realtime} className={!realtime ? 'on' : ''} onClick={() => setRealtime(false)}>Быстро</button>
            </div>
          </div>
          <ProvokeToggle showProvoke={showProvoke} setShowProvoke={setShowProvoke} />
          <Need missing={missing} />
          {busy ? <Busy busy={busy} /> : (
            <button className="btn btn-primary btn-big" disabled={!uploaded || starting || missing.length > 0}
              onClick={() => start({ source: 'file', path: uploaded.path, phone, realtime })}>
              {starting ? 'Запускаю агента…' : uploaded ? 'Разобрать запись' : 'Сначала выберите файл'}
            </button>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      </aside>
    </div>
  )
}

/** Вкладки со списком: тестовые звонки репозитория и записи событий без модели. */
function ListMode({ kind, health, busy, onStarted, showProvoke, setShowProvoke }) {
  const [sources, error] = useLoad(api.sources, [])
  const [schemes] = useLoad(api.schemes, [])
  const [picked, setPicked] = useState(null)
  const [speed, setSpeed] = useState(1)
  const { start, starting, error: startError } = useStart(onStarted)
  const names = useMemo(() => Object.fromEntries((schemes || []).map((s) => [s.id, s.name])), [schemes])

  const groups = !sources ? [] : kind === 'demo'
    ? [{
        title: 'Записанные звонки',
        note: 'Проигрываются события агента с настоящими паузами. Ollama не нужна — запасной вариант для защиты.',
        items: sources.records.map((r) => ({ key: r.path, icon: Play, title: r.title, meta: r.path.split('/').pop(), body: { source: 'replay', path: r.path } })),
      }]
    : [
        {
          title: 'Записи из репозитория',
          note: 'Синтезированные звонки по сценариям: 9 мошеннических и 3 обычных.',
          items: sources.calls.filter((c) => c.kind === 'audio').map((c) => ({
            key: c.path, icon: File, title: c.path.split('/').pop(), lfs: true,
            meta: c.scheme_id ? names[c.scheme_id] || c.scheme_id : 'без мошенничества', phone: fmtPhone(c.phone),
            body: { source: 'file', path: c.path },
          })),
        },
        {
          title: 'Реальные разговоры с мошенниками',
          note: 'Расшифровки пранк-звонков с YouTube, текстом — без распознавания речи.',
          items: sources.calls.filter((c) => c.kind === 'transcript').map((c) => ({
            key: c.path, icon: File, title: c.title.split(' — ')[1] || c.title, meta: c.path.split('/').pop(), body: { source: 'file', path: c.path },
          })),
        },
      ]
  const all = groups.flatMap((g) => g.items)
  const current = all.find((it) => it.key === picked) || all[0]
  const missing = kind === 'demo' || !current ? [] : blockers(health, { lfs: current.lfs })
  const go = () => current && start(current.body.source === 'replay' ? { ...current.body, speed } : current.body)

  return (
    <div className="newcall">
      <div className="newcall-list">
        {error && <p className="error">Не удалось получить список звонков: {error.message}</p>}
        {groups.map((g) => (
          <section key={g.title} className="group">
            <header className="group-head"><h2>{g.title}</h2><span className="count">{g.items.length}</span></header>
            <p className="group-note">{g.note}</p>
            <ul className="rows" role="listbox" aria-label={g.title}>
              {g.items.map((it) => (
                <li key={it.key}>
                  <button className={`row ${current?.key === it.key ? 'row-on' : ''}`} role="option"
                    aria-selected={current?.key === it.key} onClick={() => setPicked(it.key)} onDoubleClick={() => !missing.length && go()}>
                    <it.icon />
                    <span className="row-title">{it.title}</span>
                    <span className="row-meta">{it.meta}</span>
                    {it.phone && <span className="row-phone-end">{it.phone}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <aside className="newcall-side">
        <div className="side-card">
          <p className="side-label">Выбрано</p>
          <p className="side-title">{current ? current.title : '…'}</p>
          {kind === 'demo' && (
            <div className="field">
              <span>Скорость</span>
              <div className="segmented" role="radiogroup" aria-label="Скорость проигрывания">
                {SPEEDS.map((s) => (
                  <button key={s} role="radio" aria-checked={speed === s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>{s}×</button>
                ))}
              </div>
            </div>
          )}
          <ProvokeToggle showProvoke={showProvoke} setShowProvoke={setShowProvoke} />
          <Need missing={missing} />
          {busy ? <Busy busy={busy} /> : (
            <button className="btn btn-primary btn-wide" onClick={go} disabled={!current || starting || missing.length > 0}>
              {starting ? 'Запускаю…' : kind === 'demo' ? 'Проиграть звонок' : 'Начать звонок'}
            </button>
          )}
          {startError && <p className="error">{startError}</p>}
        </div>
      </aside>
    </div>
  )
}

/** Новый звонок: слушать вживую, загрузить запись, тестовые звонки, демо без модели. */
export default function NewCall({ tab = 'live', ...props }) {
  const active = TABS.some(([k]) => k === tab) ? tab : 'live'
  return (
    <>
      <header className="topbar">
        <h1>Новый звонок</h1>
        <nav className="modes" role="tablist" aria-label="Как начать">
          {TABS.map(([k, label, Icon]) => (
            <a key={k} role="tab" aria-selected={active === k} href={`#/new/${k}`} className={active === k ? 'on' : ''}>
              <Icon size={14} />{label}
            </a>
          ))}
        </nav>
      </header>
      {active === 'live' && <Live {...props} />}
      {active === 'upload' && <UploadMode {...props} />}
      {(active === 'tests' || active === 'demo') && <ListMode key={active} kind={active} {...props} />}
    </>
  )
}
