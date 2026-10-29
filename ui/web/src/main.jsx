import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Шрифты лежат в сборке: во время звонка интерфейс работает без интернета (ТЗ).
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono' // только для команд и текста ошибок
import './styles.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
