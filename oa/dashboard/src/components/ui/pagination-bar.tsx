import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'

const formatNumber = (value: number) => value.toLocaleString('ko-KR')

export interface PaginationBarProps {
  offset: number
  limit: number
  total: number | null
  onChange: (offset: number) => void
  label?: string
  className?: string
  size?: 'icon' | 'icon-sm'
  disabled?: boolean
  statusText?: string
}

export function PaginationBar({
  offset,
  limit,
  total,
  onChange,
  label,
  className = 'pagination',
  size = 'icon',
  disabled = false,
  statusText,
}: PaginationBarProps) {
  const prevLabel = label ? `이전 ${label} 페이지` : '이전 페이지'
  const nextLabel = label ? `다음 ${label} 페이지` : '다음 페이지'
  const summary = statusText
    ?? (total === null
      ? '— / —'
      : `${total ? `${formatNumber(offset + 1)}–${formatNumber(Math.min(offset + limit, total))}` : '0'} / ${formatNumber(total)}`)
  return (
    <div className={className}>
      <span>{summary}</span>
      <Button
        aria-label={prevLabel}
        size={size}
        variant="outline"
        disabled={disabled || total === null || offset <= 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        <ChevronLeft />
      </Button>
      <Button
        aria-label={nextLabel}
        size={size}
        variant="outline"
        disabled={disabled || total === null || offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        <ChevronRight />
      </Button>
    </div>
  )
}
