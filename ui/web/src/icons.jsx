// Иконки 16 px, линия 1.5 — одного веса с текстом интерфейса.

const Svg = ({ children, size = 16, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
    {children}
  </svg>
)

export const Phone = (p) => (
  <Svg {...p}><path d="M5.2 2.5c.5 0 .9.3 1.1.8l.7 1.8c.2.5 0 1-.4 1.3l-.9.6c.7 1.5 1.8 2.6 3.3 3.3l.6-.9c.3-.4.8-.6 1.3-.4l1.8.7c.5.2.8.6.8 1.1v1.4c0 .7-.6 1.3-1.3 1.3C6.6 13.5 2.5 9.4 2.5 3.8c0-.7.6-1.3 1.3-1.3z" /></Svg>
)
export const PhoneOff = (p) => (
  <Svg {...p}><path d="M2.8 9.6c2.9-2.7 7.5-2.7 10.4 0 .4.4.4 1 0 1.4l-1 1c-.3.3-.8.4-1.2.2l-1.6-.7c-.4-.2-.6-.5-.6-.9V9.5c-1.2-.4-2.4-.4-3.6 0v1.1c0 .4-.2.7-.6.9l-1.6.7c-.4.2-.9.1-1.2-.2l-1-1c-.4-.4-.4-1 0-1.4z" /></Svg>
)
export const History = (p) => (
  <Svg {...p}><path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9" /><path d="M2.5 2.5v2.2h2.2" /><path d="M8 5v3.2l2 1.3" /></Svg>
)
export const Book = (p) => (
  <Svg {...p}><path d="M3 3.2c0-.4.3-.7.7-.7H7c.6 0 1 .4 1 1v9.5c0-.6-.4-1-1-1H3.7c-.4 0-.7-.3-.7-.7z" /><path d="M13 3.2c0-.4-.3-.7-.7-.7H9c-.6 0-1 .4-1 1v9.5c0-.6.4-1 1-1h3.3c.4 0 .7-.3.7-.7z" /></Svg>
)
export const Plus = (p) => <Svg {...p}><path d="M8 3.5v9M3.5 8h9" /></Svg>
export const Search = (p) => <Svg {...p}><circle cx="7" cy="7" r="4.25" /><path d="m10.2 10.2 3.3 3.3" /></Svg>
export const Stop = (p) => <Svg {...p}><rect x="4" y="4" width="8" height="8" rx="1.5" /></Svg>
export const ChevronLeft = (p) => <Svg {...p}><path d="m9.5 4-4 4 4 4" /></Svg>
export const ChevronRight = (p) => <Svg {...p}><path d="m6.5 4 4 4-4 4" /></Svg>
export const ChevronDown = (p) => <Svg {...p}><path d="m4 6.5 4 4 4-4" /></Svg>
export const Alert = (p) => (
  <Svg {...p}><path d="M7.1 2.8a1 1 0 0 1 1.8 0l4.9 8.7c.4.7-.1 1.5-.9 1.5H3.1c-.8 0-1.3-.8-.9-1.5z" /><path d="M8 6.3v2.6" /><circle cx="8" cy="11" r=".4" fill="currentColor" /></Svg>
)
export const Message = (p) => (
  <Svg {...p}><path d="M3 4.2c0-.7.5-1.2 1.2-1.2h7.6c.7 0 1.2.5 1.2 1.2v5.1c0 .7-.5 1.2-1.2 1.2H7l-2.7 2.3v-2.3h-.1c-.7 0-1.2-.5-1.2-1.2z" /></Svg>
)
export const Target = (p) => <Svg {...p}><circle cx="8" cy="8" r="5.25" /><circle cx="8" cy="8" r="2.25" /></Svg>
export const Steps = (p) => <Svg {...p}><path d="M2.5 12.5h3.5V9h3.5V5.5h4" /></Svg>
export const Database = (p) => (
  <Svg {...p}><ellipse cx="8" cy="4" rx="4.75" ry="1.75" /><path d="M3.25 4v8c0 1 2.1 1.75 4.75 1.75S12.75 13 12.75 12V4" /><path d="M3.25 8c0 1 2.1 1.75 4.75 1.75S12.75 9 12.75 8" /></Svg>
)
export const Doc = (p) => (
  <Svg {...p}><path d="M4 2.5h5l3 3v7.3c0 .4-.3.7-.7.7H4c-.4 0-.7-.3-.7-.7V3.2c0-.4.3-.7.7-.7z" /><path d="M9 2.5v3h3M5.5 8.5h5M5.5 11h3.5" /></Svg>
)
export const Clock = (p) => <Svg {...p}><circle cx="8" cy="8" r="5.25" /><path d="M8 5v3.2l2 1.3" /></Svg>
export const Calendar = (p) => (
  <Svg {...p}><rect x="2.75" y="3.5" width="10.5" height="9.75" rx="1.5" /><path d="M2.75 6.5h10.5M5.5 2.25v2.5M10.5 2.25v2.5" /></Svg>
)
export const Mic = (p) => (
  <Svg {...p}><rect x="6" y="2.5" width="4" height="7" rx="2" /><path d="M3.75 7.5a4.25 4.25 0 0 0 8.5 0M8 11.75v1.75" /></Svg>
)
export const Speaker = (p) => (
  <Svg {...p}><path d="M3 6.2v3.6h2.2L8.5 12.5v-9L5.2 6.2z" /><path d="M10.8 5.5a3.5 3.5 0 0 1 0 5M12.6 3.8a6 6 0 0 1 0 8.4" /></Svg>
)
export const Play = (p) => <Svg {...p}><path d="M5 3.5v9l7.5-4.5z" /></Svg>
export const File = (p) => (
  <Svg {...p}><path d="M4 2.5h5l3 3v7.3c0 .4-.3.7-.7.7H4c-.4 0-.7-.3-.7-.7V3.2c0-.4.3-.7.7-.7z" /><path d="M9 2.5v3h3" /></Svg>
)
export const Upload = (p) => <Svg {...p}><path d="M8 10.5V2.75M5 5.5l3-3 3 3" /><path d="M2.75 10v2.25c0 .55.45 1 1 1h8.5c.55 0 1-.45 1-1V10" /></Svg>
export const Wave = (p) => <Svg {...p}><path d="M2.5 8h1.5M5.5 5.5v5M8 3.5v9M10.5 6v4M12.5 8H14" /></Svg>
export const External = (p) => <Svg {...p}><path d="M9 3h4v4M13 3 7.5 8.5M11.5 9.5v3c0 .3-.2.5-.5.5H3.5c-.3 0-.5-.2-.5-.5V5c0-.3.2-.5.5-.5h3" /></Svg>

/** Статус этапа в стиле трекера задач: пройден, идёт сейчас, впереди. */
export function StageIcon({ state }) {
  if (state === 'done')
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6.25" fill="var(--text-3)" />
        <path d="m5.4 8.2 1.8 1.8 3.4-3.6" fill="none" stroke="var(--panel)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  if (state === 'current')
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6" fill="none" stroke="var(--red)" strokeWidth="1.5" />
        <path d="M8 4a4 4 0 0 1 0 8z" fill="var(--red)" />
      </svg>
    )
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--text-3)" strokeWidth="1.5" strokeDasharray="2.2 2.2" />
    </svg>
  )
}
