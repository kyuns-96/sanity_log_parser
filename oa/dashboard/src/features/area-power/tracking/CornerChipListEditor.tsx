import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '../components/workspace'
import { cornerSpecId, type CornerSpec } from '../trackingApi'

interface CornerChipListEditorProps {
  corners: CornerSpec[]
  onChange: (next: CornerSpec[]) => void
  canWrite: boolean
  emptyText: string
  placeholder: string
  allowPvtDetail?: boolean
  removeLabel?: boolean
}

export function CornerChipListEditor({
  corners,
  onChange,
  canWrite,
  emptyText,
  placeholder,
  allowPvtDetail = false,
  removeLabel = false,
}: CornerChipListEditorProps) {
  const [name, setName] = useState('')
  const [showDetail, setShowDetail] = useState(false)
  const [volt, setVolt] = useState('')
  const [temp, setTemp] = useState('')
  const [pex, setPex] = useState('')

  function addCorner() {
    const trimmed = name.trim()
    if (!trimmed) return
    onChange([
      ...corners,
      {
        label: trimmed,
        process_label: trimmed,
        voltage_v: allowPvtDetail ? volt.trim() || null : null,
        temp_c: allowPvtDetail ? temp.trim() || null : null,
        pex: allowPvtDetail ? pex.trim() || null : null,
      },
    ])
    setName('')
    setVolt('')
    setTemp('')
    setPex('')
  }

  return (
    <>
      <div className="tracking-chip-list">
        {corners.map((c, idx) => (
          <span key={cornerSpecId(c)} className="tracking-chip">
            {c.label}
            {(c.voltage_v || c.temp_c || c.pex) && (
              <small>
                ({[c.voltage_v && `${c.voltage_v}V`, c.temp_c && `${c.temp_c}°C`, c.pex].filter(Boolean).join(' / ')})
              </small>
            )}
            {canWrite && (
              <button
                type="button"
                aria-label={removeLabel ? `${c.label} 삭제` : undefined}
                onClick={() => onChange(corners.filter((_, i) => i !== idx))}
              >
                <Trash2 size={12} />
              </button>
            )}
          </span>
        ))}
        {!corners.length && <small className="text-muted">{emptyText}</small>}
      </div>
      {canWrite && (
        <div className="settings-corner-add">
          <input
            placeholder={placeholder}
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCorner() } }}
          />
          <Button variant="outline" size="sm" onClick={addCorner}>
            <Plus size={14} />추가
          </Button>
          {allowPvtDetail && (
            <button type="button" className="tracking-link-inline" onClick={() => setShowDetail(v => !v)}>
              {showDetail ? '세부 조건 숨기기' : '전압·온도 지정'}
            </button>
          )}
        </div>
      )}
      {canWrite && allowPvtDetail && showDetail && (
        <div className="tracking-inline-add">
          <input placeholder="전압 V (예: 0.70)" value={volt} onChange={e => setVolt(e.target.value)} />
          <input placeholder="온도 °C (예: 25)" value={temp} onChange={e => setTemp(e.target.value)} />
          <input placeholder="PEX (선택)" value={pex} onChange={e => setPex(e.target.value)} />
        </div>
      )}
    </>
  )
}
