import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { HelpCircle } from 'lucide-react'

export interface LiveEpsItem {
  label: string
  value: string
}

export function EffectiveFormulaHint({
  label = 'Effective Δ%',
  compact = false,
  noiseSharePct = '0.1',
  thresholdPct = '10',
  liveEps,
}: {
  label?: string
  compact?: boolean
  noiseSharePct?: string
  thresholdPct?: string
  liveEps?: LiveEpsItem[]
}) {
  const triggerRef = useRef<HTMLSpanElement>(null)
  const closeTimer = useRef<number | undefined>(undefined)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number; placement: 'below' | 'above' }>({
    top: 0,
    left: 0,
    placement: 'below',
  })

  const sNum = Number(noiseSharePct)
  const pNum = Number(thresholdPct)
  const epsPctText = Number.isFinite(sNum) && Number.isFinite(pNum) && pNum > 0
    ? `${((sNum / pNum) * 100).toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1')}%`
    : '1.0%'

  function updatePosition() {
    const el = triggerRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const cardW = 352
    const estH = liveEps && liveEps.length ? 268 : 220
    const margin = 10
    const spaceBelow = window.innerHeight - rect.bottom
    const placement: 'below' | 'above' = spaceBelow >= estH + margin || spaceBelow >= rect.top ? 'below' : 'above'
    const rawLeft = rect.left + rect.width / 2 - cardW / 2
    const left = Math.max(margin, Math.min(rawLeft, window.innerWidth - cardW - margin))
    const top = placement === 'below' ? rect.bottom + 8 : Math.max(margin, rect.top - estH - 8)
    setPos({ top, left, placement })
  }

  function show() {
    window.clearTimeout(closeTimer.current)
    updatePosition()
    setOpen(true)
  }

  function scheduleClose() {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setOpen(false), 120)
  }

  useEffect(() => {
    if (!open) return
    const onScrollOrResize = () => updatePosition()
    window.addEventListener('scroll', onScrollOrResize, true)
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [open])

  useEffect(() => () => window.clearTimeout(closeTimer.current), [])

  return (
    <>
      <span
        ref={triggerRef}
        className={`effective-hint-trigger${compact ? ' is-compact' : ''}`}
        tabIndex={0}
        role="button"
        aria-label="Effective Δ% 계산식 보기"
        aria-expanded={open}
        onMouseEnter={show}
        onMouseLeave={scheduleClose}
        onFocus={show}
        onBlur={scheduleClose}
        onClick={event => {
          event.stopPropagation()
          if (open) setOpen(false)
          else show()
        }}
      >
        {!compact && <span className="effective-hint-label">{label}</span>}
        <HelpCircle size={compact ? 12 : 13} aria-hidden="true" />
      </span>
      {open && typeof document !== 'undefined' && createPortal(
        <div
          className={`effective-formula-popover placement-${pos.placement}`}
          role="tooltip"
          style={{ top: pos.top, left: pos.left }}
          onMouseEnter={() => window.clearTimeout(closeTimer.current)}
          onMouseLeave={scheduleClose}
        >
          <div className="eff-pop-head">
            <strong>Effective Δ%</strong>
            <span>규모 완충 변화율</span>
          </div>
          <div className="eff-formula-box" aria-label="Effective Δ% 계산식">
            <div className="eff-eq-main">
              <span className="eff-lhs">Effective Δ%</span>
              <span className="eff-op">=</span>
              <span className="eff-frac">
                <span className="eff-num"><em>C</em> − <em>B</em></span>
                <span className="eff-den"><em>B</em> + <em>ε</em></span>
              </span>
              <span className="eff-op">× 100%</span>
            </div>
            <div className="eff-eq-sub">
              <span><em>ε</em> = ({noiseSharePct}% / {thresholdPct}%) × Root = <strong>{epsPctText} × Root</strong></span>
            </div>
          </div>
          <dl className="eff-legend">
            <div><dt><em>B</em>, <em>C</em></dt><dd>기준 · 현재 값 (신규 항목은 <em>B</em> = 0)</dd></div>
            <div><dt><em>ε</em></dt><dd>루트 전체(Root) 규모에 비례하는 분모 완충값</dd></div>
          </dl>
          {liveEps && liveEps.length > 0 && (
            <div className="eff-live-eps">
              <span className="eff-live-title">현재 비교 완충값 (ε)</span>
              <div className="eff-live-chips">
                {liveEps.map(item => (
                  <span key={item.label} className="eff-live-chip">
                    {item.label} <strong>{item.value}</strong>
                  </span>
                ))}
              </div>
            </div>
          )}
          <p className="eff-pop-note">
            기본 <strong>Effective Δ%</strong> 모드에서는 완충된 값을 표시·정렬하며, 원시 비율은 <strong>단순 Δ%</strong> 탭이나 셀 툴팁에서 바로 확인할 수 있습니다. (예: 0.01 → 0.06 mW = 단순 +500%, Effective +6.7%)
          </p>
        </div>,
        document.body,
      )}
    </>
  )
}
