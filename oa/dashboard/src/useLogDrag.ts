import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent, RefObject } from 'react'

export const NEW_LOG_GROUP = '__new_log_group__'
export type LogMove = { ids: string[]; sourceGroup: string; revision: number; preview: string }
type DragState = LogMove & { target: string | null; keyboard: boolean; x: number; y: number }
type PendingDrag = LogMove & { pointer: number; x: number; y: number; startX: number; startY: number; active: boolean }

// Only the grip captures touch gestures; the rest of each log remains scrollable.
// IDs and revision are captured at pickup, and the server validates the full move.
export function useLogDrag({ scope, enabled, revision, onDrop }: {
  scope: RefObject<HTMLElement | null>; enabled: boolean; revision: number;
  onDrop: (move: LogMove, target: string) => void
}) {
  const helpId = useId()
  const [drag, setDrag] = useState<DragState | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const current = useRef<DragState | null>(null)
  const pending = useRef<PendingDrag | null>(null)
  const pickup = useRef<HTMLButtonElement | null>(null)
  const latest = useRef({ enabled, revision, onDrop })
  latest.current = { enabled, revision, onDrop }

  function update(value: DragState | null) { current.current = value; setDrag(value) }
  function cancel(message = '로그 이동을 취소했습니다.') {
    const handle = pickup.current
    pending.current = null
    pickup.current = null
    update(null)
    setAnnouncement(message)
    if (handle?.isConnected) handle.focus({ preventScroll: true })
  }
  function targets() {
    return [...(scope.current?.querySelectorAll<HTMLElement>('button[data-log-drop]:not([aria-disabled="true"])') || [])]
  }
  function targetAt(x: number, y: number) {
    const element = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-log-drop]')
    return element && scope.current?.contains(element) && element.getAttribute('aria-disabled') !== 'true'
      ? element.dataset.logDrop || null : null
  }
  function finish(target: string | null) {
    const value = current.current
    pending.current = null
    pickup.current = null
    update(null)
    if (!value || !target || !latest.current.enabled || value.revision !== latest.current.revision) {
      setAnnouncement('로그 이동을 취소했습니다.'); return
    }
    if (target === value.sourceGroup) {
      setAnnouncement('현재 그룹입니다. 로그 구성을 유지했습니다.'); return
    }
    latest.current.onDrop(value, target)
  }

  useEffect(() => {
    if (!enabled || current.current?.revision !== revision) {
      if (pending.current || current.current) cancel()
    }
  }, [enabled, revision])

  useEffect(() => {
    let frame = 0
    function scroll() {
      const value = pending.current
      if (!value?.active) { frame = 0; return }
      const rail = scope.current?.querySelector<HTMLElement>('.ws-workbench-group-list')
      if (rail) {
        const rect = rail.getBoundingClientRect()
        if (value.x >= rect.left && value.x <= rect.right && value.y >= rect.top && value.y <= rect.bottom) {
          const delta = value.y < rect.top+40 ? -9 : value.y > rect.bottom-40 ? 9 : 0
          if (delta) rail.scrollTop += delta
        }
      }
      if (value.y < 70) window.scrollBy(0, -10)
      else if (value.y > window.innerHeight-70) window.scrollBy(0, 10)
      const target = targetAt(value.x, value.y)
      if (current.current && current.current.target !== target) update({ ...current.current, target })
      frame = requestAnimationFrame(scroll)
    }
    function move(event: globalThis.PointerEvent) {
      const value = pending.current
      if (!value || value.pointer !== event.pointerId) return
      if (!latest.current.enabled || value.revision !== latest.current.revision) { cancel(); return }
      value.x = event.clientX; value.y = event.clientY
      if (!value.active && Math.hypot(value.x-value.startX, value.y-value.startY) < 6) return
      event.preventDefault()
      if (!value.active) {
        value.active = true
        setAnnouncement(`${value.ids.length.toLocaleString('ko-KR')}건을 이동합니다. 왼쪽 그룹에 놓으세요. Escape로 취소합니다.`)
      }
      update({ ...value, keyboard: false, target: targetAt(value.x, value.y) })
      if (!frame) frame = requestAnimationFrame(scroll)
    }
    function up(event: globalThis.PointerEvent) {
      if (pending.current?.pointer !== event.pointerId) return
      if (pending.current.active) { event.preventDefault(); finish(targetAt(event.clientX, event.clientY)) }
      else { pending.current = null; pickup.current = null }
    }
    function pointerCancel(event: globalThis.PointerEvent) {
      if (pending.current?.pointer === event.pointerId) cancel()
    }
    function key(event: globalThis.KeyboardEvent) {
      const value = current.current
      if (!value && !pending.current) return
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); return }
      if (!value?.keyboard) return
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); event.stopPropagation()
        const options = targets().filter(element => element.dataset.logDrop !== value.sourceGroup)
        if (!options.length) return
        const index = options.findIndex(element => element.dataset.logDrop === value.target)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length-1
          : (index+(event.key === 'ArrowUp' ? -1 : 1)+options.length) % options.length
        const element = options[next]
        element.focus({ preventScroll: true }); element.scrollIntoView({ block: 'nearest' })
        update({ ...value, target: element.dataset.logDrop || null })
        setAnnouncement(`${element.dataset.dropLabel} 그룹. Enter로 이동합니다.`)
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); event.stopPropagation(); finish(value.target)
      } else if (event.key === 'Tab') {
        cancel('키보드 이동을 종료했습니다. 선택 로그의 이동 버튼도 사용할 수 있습니다.')
      }
    }
    function blur() { if (current.current || pending.current) cancel() }
    window.addEventListener('pointermove', move, { passive: false })
    window.addEventListener('pointerup', up, { passive: false })
    window.addEventListener('pointercancel', pointerCancel)
    window.addEventListener('keydown', key, true)
    window.addEventListener('blur', blur)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', pointerCancel)
      window.removeEventListener('keydown', key, true)
      window.removeEventListener('blur', blur)
    }
  }, [])

  function pointerDown(event: PointerEvent<HTMLButtonElement>, value: LogMove) {
    if (!enabled || event.button !== 0 || !event.isPrimary || current.current) return
    pickup.current = event.currentTarget
    event.currentTarget.setPointerCapture(event.pointerId)
    pending.current = { ...value, pointer: event.pointerId, x: event.clientX, y: event.clientY,
      startX: event.clientX, startY: event.clientY, active: false }
  }
  function keyDown(event: KeyboardEvent<HTMLButtonElement>, value: LogMove) {
    if (!enabled || ![' ', 'Enter'].includes(event.key) || current.current) return
    event.preventDefault()
    pickup.current = event.currentTarget
    const target = targets().find(element => element.dataset.logDrop !== value.sourceGroup)
    update({ ...value, target: target?.dataset.logDrop || null, keyboard: true, x: 0, y: 0 })
    target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: 'nearest' })
    setAnnouncement(`${value.ids.length.toLocaleString('ko-KR')}건 선택. ${target?.dataset.dropLabel || '새 그룹'}. 방향키로 그룹을 고르고 Enter로 이동, Escape로 취소합니다.`)
  }
  return { drag, helpId, announcement, pointerDown, keyDown, cancel }
}
