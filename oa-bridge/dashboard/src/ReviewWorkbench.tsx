import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronLeft } from 'lucide-react'

const compactQuery = '(max-width: 1100px)'

export function ReviewWorkbench({ ledger, inspection, selection, inspectionOpen, onBack }: {
  ledger: ReactNode
  inspection: ReactNode
  selection: string
  inspectionOpen: boolean
  onBack: () => void
}) {
  const [compact, setCompact] = useState(() => window.matchMedia(compactQuery).matches)
  const ledgerRef = useRef<HTMLElement>(null)
  const inspectionRef = useRef<HTMLElement>(null)
  const wasInspecting = useRef(false)

  useEffect(() => {
    const query = window.matchMedia(compactQuery)
    const update = () => setCompact(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (compact && inspectionOpen) {
      inspectionRef.current?.querySelector<HTMLElement>('.unit-detail-heading h3')?.focus()
    } else if (compact && wasInspecting.current) {
      ledgerRef.current?.querySelector<HTMLElement>('.unit-select[aria-pressed="true"]')?.focus()
    }
    wasInspecting.current = compact && inspectionOpen
  }, [compact, inspectionOpen, selection])

  return <div className="review-split">
    <section ref={ledgerRef} className="review-ledger" aria-label="검사 대장" hidden={compact && inspectionOpen}>
      {ledger}
    </section>
    <section ref={inspectionRef} className="review-inspection" aria-label="선택 항목 근거" hidden={compact && !inspectionOpen}>
      {compact && <button className="inspection-back" onClick={onBack}><ChevronLeft size={16} />검사 목록으로</button>}
      {inspection}
    </section>
  </div>
}
