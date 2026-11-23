export type ColorMode = 'system' | 'light' | 'dark'
type ColorModeState = { preference: ColorMode; resolved: 'light' | 'dark' }

const storageKey = 'sanity-color-mode'
const listeners = new Set<() => void>()
let state: ColorModeState = { preference: 'system', resolved: 'light' }
let media: MediaQueryList
let initialized = false

function readPreference(): ColorMode {
  try {
    const value = localStorage.getItem(storageKey)
    if (value === 'light' || value === 'dark') return value
  } catch { /* Theme selection also works when browser storage is unavailable. */ }
  return 'system'
}

function apply(preference: ColorMode) {
  const resolved = preference === 'system' ? media.matches ? 'dark' : 'light' : preference
  const root = document.documentElement
  root.dataset.theme = resolved
  root.classList.toggle('dark', resolved === 'dark')
  root.style.colorScheme = resolved
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#141323' : '#f8f7ff')
  if (state.preference !== preference || state.resolved !== resolved) {
    state = { preference, resolved }
    listeners.forEach(listener => listener())
  }
}

export function initializeColorMode() {
  if (initialized) return
  initialized = true
  media = window.matchMedia('(prefers-color-scheme: dark)')
  apply(readPreference())
  media.addEventListener('change', () => {
    if (state.preference === 'system') apply('system')
  })
  window.addEventListener('storage', event => {
    if (event.key === storageKey || event.key === null) apply(readPreference())
  })
}

export function setColorMode(preference: ColorMode) {
  try {
    if (preference === 'system') localStorage.removeItem(storageKey)
    else localStorage.setItem(storageKey, preference)
  } catch { /* Keep the choice for the current page if storage is blocked. */ }
  apply(preference)
}

export const getColorMode = () => state
export function subscribeColorMode(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
