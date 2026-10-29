// Как числа, время и номера выглядят на экране.

export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

/** «+79990000002» → «+7 999 000-00-02». Неизвестный номер — null. */
export function phone(raw) {
  const m = /^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/.exec(raw || '')
  return m ? `+7 ${m[1]} ${m[2]}-${m[3]}-${m[4]}` : raw || null
}

/** plural(2, ['раз', 'раза', 'раз']) → «раза». */
export function plural(n, [one, few, many]) {
  const d = Math.abs(n) % 100
  const u = d % 10
  if (d > 10 && d < 20) return many
  if (u === 1) return one
  if (u >= 2 && u <= 4) return few
  return many
}

/** Секунды с запятой: 0.3 → «0,3 с», 19.4 → «19 с». */
export function seconds(x) {
  if (x == null) return ''
  return x < 10 ? `${x.toFixed(1).replace('.', ',')} с` : `${Math.round(x)} с`
}

export function ms(x) {
  return x == null ? '' : x < 1000 ? `${Math.round(x)} мс` : seconds(x / 1000)
}

const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })

/** «29 окт., 22:33»; сегодняшняя дата — «сегодня, 22:33». */
export function when(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const today = new Date()
  const day = d.toDateString() === today.toDateString() ? 'сегодня' : dateFmt.format(d)
  return `${day}, ${timeFmt.format(d)}`
}

export function duration(startIso, endIso) {
  if (!startIso || !endIso) return ''
  return clock((Date.parse(endIso) - Date.parse(startIso)) / 1000)
}

export const CATEGORIES = {
  bank: 'Банк',
  gosuslugi: 'Госуслуги и госорганы',
  relative: 'Родственники',
  investment: 'Инвестиции',
  delivery: 'Покупки и доставка',
  other: 'Другое',
}

export const TACTICS = {
  play_dumb: 'тупить',
  fake_compliance: 'ложное согласие',
  absurd_story: 'абсурдная история',
  role_reversal: 'перехват роли',
  mockery: 'издёвка',
}

export const KINDS = {
  stall: 'тянуть время',
  question: 'уточнить',
  provoke: 'провокация',
  hangup: 'выход',
}

export const SPEAKERS = { caller: 'Звонящий', user: 'Вы', unknown: 'Голос' }

export const TAGS = { fraud: 'мошенник', bank: 'банк', delivery: 'доставка', investment: 'инвестиции', 'courier-ok': 'настоящий курьер' }
