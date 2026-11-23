import { useEffect, useRef, useState } from 'react'
import { Settings2, UserRound } from 'lucide-react'
import './sidebar-mode-switch.css'

export type DashboardMode = 'user' | 'admin'

export function SidebarModeSwitch({ mode, onChange }: { mode: DashboardMode; onChange: (mode: DashboardMode) => void | Promise<void> }) {
  const [selected, setSelected] = useState(mode)
  const [moving, setMoving] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const pending = useRef(false)
  const active = useRef(true)
  const [error, setError] = useState('')
  useEffect(() => {
    active.current = true
    clearTimeout(timer.current); setSelected(mode); setMoving(false); setError(''); pending.current = false
    return () => { active.current = false; clearTimeout(timer.current) }
  }, [mode])

  function choose(next: DashboardMode) {
    if (next === mode || pending.current) return
    pending.current = true
    setSelected(next); setMoving(true); setError('')
    const delay = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 220
    timer.current = setTimeout(async () => {
      try { await onChange(next) }
      catch (error) {
        if (active.current) setError(error instanceof TypeError ? '연결 상태를 확인하고 다시 시도해 주세요.' : error instanceof Error ? error.message : '모드를 전환하지 못했습니다.')
      } finally {
        if (active.current) { setSelected(mode); setMoving(false); pending.current = false }
      }
    }, delay)
  }

  return <div className="sidebar-mode-switch" data-mode={selected} aria-busy={moving}>
    <div className="sidebar-mode-expanded" role="group" aria-label="작업 모드">
      <span className="sidebar-mode-thumb" aria-hidden="true" />
      <button type="button" aria-label="사용자 모드" aria-pressed={selected === 'user'} disabled={moving} onClick={() => choose('user')}><UserRound size={15} aria-hidden="true" /><span>사용자</span></button>
      <button type="button" aria-label="관리자 모드" aria-pressed={selected === 'admin'} disabled={moving} onClick={() => choose('admin')}><Settings2 size={15} aria-hidden="true" /><span>관리자</span></button>
    </div>
    <button type="button" className="sidebar-mode-compact" aria-label={`${mode === 'user' ? '관리자' : '사용자'} 모드로 전환`} title={`${mode === 'user' ? '관리자' : '사용자'} 모드로 전환`} disabled={moving} onClick={() => choose(mode === 'user' ? 'admin' : 'user')}>{mode === 'user' ? <Settings2 size={18} aria-hidden="true" /> : <UserRound size={18} aria-hidden="true" />}</button>
    {error && <p className="sidebar-mode-error" role="alert">{error}</p>}
  </div>
}
