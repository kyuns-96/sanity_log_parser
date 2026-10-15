import { useEffect, useRef, useState } from 'react'

const mobileQuery = '(max-width: 720px)'

export function useWorkspaceNavigation(open: boolean, close: () => void, active: boolean) {
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(mobileQuery).matches)
  const sidebarRef = useRef<HTMLElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const query = window.matchMedia(mobileQuery)
    const update = () => { setIsMobile(query.matches); if (!query.matches) close() }
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [close])

  useEffect(() => {
    if (!open || !isMobile || !active) return
    const sidebar = sidebarRef.current
    const trigger = triggerRef.current
    if (!sidebar) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () => Array.from(sidebar.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]'))
      .filter(element => element.getClientRects().length > 0)
    focusable()[0]?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); close(); return }
      if (event.key !== 'Tab') return
      const elements = focusable()
      const first = elements[0], last = elements.at(-1)
      if (event.shiftKey && (document.activeElement === first || !sidebar?.contains(document.activeElement))) {
        event.preventDefault(); last?.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !sidebar?.contains(document.activeElement))) {
        event.preventDefault(); first?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
      trigger?.focus()
    }
  }, [open, isMobile, active, close])

  return { isMobile, sidebarRef, triggerRef }
}
