import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { CSSProperties, KeyboardEvent, PointerEvent } from 'react'

const minimum = 216
const maximum = 360
const defaultWidth = 220
const storageKey = 'sanity-sidebar-width'
const clamp = (value: number, upper = maximum) => Math.round(Math.max(minimum, Math.min(upper, value)))
function storedWidth(value: string | null): number {
  const number = value === null || value.trim() === '' ? NaN : Number(value)
  return Number.isFinite(number) ? clamp(number) : defaultWidth
}
function initialWidth(): number {
  try { return storedWidth(localStorage.getItem(storageKey)) } catch { return defaultWidth }
}
// Only a numeric presentation preference is shared; no account or report data.
let preferredWidth = initialWidth()
const listeners = new Set<() => void>()
function updateWidth(value: number, persist = true) {
  const next = clamp(value)
  if (next !== preferredWidth) {
    preferredWidth = next
    listeners.forEach(listener => listener())
  }
  if (persist) { try { localStorage.setItem(storageKey, String(next)) } catch { /* Storage can be disabled. */ } }
}
function storageChanged(event: StorageEvent) {
  if (event.key === storageKey || event.key === null) updateWidth(storedWidth(event.newValue), false)
}
function subscribe(listener: () => void) {
  listeners.add(listener)
  if (listeners.size === 1) window.addEventListener('storage', storageChanged)
  return () => {
    listeners.delete(listener)
    if (!listeners.size) window.removeEventListener('storage', storageChanged)
  }
}
const snapshot = () => preferredWidth
const viewportMaximum = () => Math.max(minimum, Math.min(maximum, window.innerWidth - 720))
type Drag = { id: number; x: number; width: number; previous: number; next: number; target: HTMLDivElement }

export function useSidebarLayout(enabled: boolean) {
  const preferred = useSyncExternalStore(subscribe, snapshot, () => defaultWidth)
  const [upper, setUpper] = useState(viewportMaximum)
  const width = clamp(preferred, upper)
  const [resizing, setResizing] = useState(false)
  const drag = useRef<Drag | null>(null)
  const frame = useRef(0)

  const stop = useCallback((cancel: boolean) => {
    const current = drag.current
    if (!current) return
    drag.current = null
    cancelAnimationFrame(frame.current); frame.current = 0
    document.documentElement.classList.remove('sidebar-pointer-resizing')
    updateWidth(cancel ? current.previous : current.next)
    setResizing(false)
    if (current.target.hasPointerCapture(current.id)) current.target.releasePointerCapture(current.id)
  }, [])

  useEffect(() => {
    const resize = () => { stop(true); setUpper(viewportMaximum()) }
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); stop(true) }
  }, [stop])
  useEffect(() => { if (!enabled) stop(true) }, [enabled, stop])

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!enabled || event.button !== 0 || !event.isPrimary || drag.current) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    event.currentTarget.focus({ preventScroll: true })
    drag.current = { id: event.pointerId, x: event.clientX, width, previous: preferredWidth, next: width, target: event.currentTarget }
    document.documentElement.classList.add('sidebar-pointer-resizing')
    setResizing(true)
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current
    if (!current || current.id !== event.pointerId) return
    current.next = clamp(current.width + event.clientX - current.x, viewportMaximum())
    // At most one React/layout update per frame; persist only when dragging ends.
    if (!frame.current) frame.current = requestAnimationFrame(() => {
      frame.current = 0
      if (drag.current) updateWidth(drag.current.next, false)
    })
  }
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && drag.current) { event.preventDefault(); stop(true); return }
    if (!enabled) return
    const step = event.shiftKey ? 24 : 8
    const next = event.key === 'Home' ? minimum : event.key === 'End' ? upper
      : event.key === 'ArrowLeft' ? width - step : event.key === 'ArrowRight' ? width + step : null
    if (next === null) return
    event.preventDefault()
    updateWidth(clamp(next, upper))
  }

  return {
    resizing,
    style: { '--sidebar-expanded-width': `${width}px` } as CSSProperties,
    handleProps: {
      role: 'separator', tabIndex: 0, 'aria-label': '사이드바 너비 조절',
      'aria-orientation': 'vertical' as const, 'aria-valuemin': minimum, 'aria-valuemax': upper,
      'aria-valuenow': width, 'aria-valuetext': `${width}픽셀`,
      title: '드래그로 너비 조절 · 두 번 클릭하면 기본 너비',
      onPointerDown, onPointerMove, onKeyDown,
      onPointerUp: (event: PointerEvent<HTMLDivElement>) => { if (drag.current?.id === event.pointerId) stop(false) },
      onPointerCancel: (event: PointerEvent<HTMLDivElement>) => { if (drag.current?.id === event.pointerId) stop(true) },
      onLostPointerCapture: (event: PointerEvent<HTMLDivElement>) => { if (drag.current?.id === event.pointerId) stop(true) },
      onDoubleClick: () => updateWidth(defaultWidth),
    },
  }
}

export function SidebarResizer({ layout }: { layout: ReturnType<typeof useSidebarLayout> }) {
  return <div className="sidebar-resizer" {...layout.handleProps} />
}
