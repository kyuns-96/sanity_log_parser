import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Check, LoaderCircle } from 'lucide-react'
import { Button } from '../components/workspace'
import type { useHierarchy } from './hierarchyUtils'

interface PathPickerProps {
  label: string
  hierarchy: ReturnType<typeof useHierarchy>
  value: string
  onChange: (value: string) => void
  onReload: () => void
  disabled: boolean
}

export function PathPicker({ label, hierarchy, value, onChange, onReload, disabled }: PathPickerProps) {
  const id = useId()
  const root = useRef<HTMLElement>(null)
  const focusResults = useRef(false)
  const [page, setPage] = useState(0)
  const [open, setOpen] = useState(false)
  const searching = !!value.trim()
  const matches = useMemo(() => {
    const terms = value.toLowerCase().split(/\s+/).filter(Boolean)
    return terms.length ? (hierarchy.data?.items || []).filter(row => terms.every(term => row.path.toLowerCase().includes(term))) : []
  }, [hierarchy.data, value])
  const selected = !!hierarchy.data?.items.some(row => row.path === value)
  const pageSize = 50
  const lastPage = Math.max(0, Math.ceil(matches.length / pageSize) - 1)
  const currentPage = Math.min(page, lastPage)
  const start = currentPage * pageSize
  const visible = matches.slice(start, start + pageSize)
  useEffect(() => {
    if (open && focusResults.current) { focusResults.current = false; root.current?.querySelector<HTMLButtonElement>('.path-results button')?.focus() }
  }, [open])
  function choose(path: string) { setPage(0); onChange(path); root.current?.querySelector<HTMLInputElement>('input')?.focus(); setOpen(false) }
  return <section ref={root} className="path-picker" aria-labelledby={`${id}-label`} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false) }}>
    <label className="field" htmlFor={`${id}-search`}><span id={`${id}-label`}>{label}</span><input id={`${id}-search`} type="search" role="combobox" aria-autocomplete="list" aria-expanded={open && searching} aria-controls={`${id}-results`} value={value} placeholder="경로 일부 검색 또는 전체 경로 붙여넣기" autoComplete="off" spellCheck={false} disabled={disabled || hierarchy.loading || !!hierarchy.error || !hierarchy.data?.items.length} aria-describedby={`${id}-status`} onFocus={() => setOpen(true)} onChange={event => { setPage(0); setOpen(true); onChange(event.target.value) }} onKeyDown={event => { if (event.key === 'Escape') setOpen(false); if (event.key === 'ArrowDown' && searching) { event.preventDefault(); if (open) root.current?.querySelector<HTMLButtonElement>('.path-results button')?.focus(); else { focusResults.current = true; setOpen(true) } } if (event.key === 'Enter' && matches.length === 1) { event.preventDefault(); choose(matches[0].path) } }} /></label>
    {hierarchy.loading ? <p id={`${id}-status`} role="status"><LoaderCircle size={15} className="spin" />{label} 조회 중…</p> : hierarchy.error ? <div className="notice error" id={`${id}-status`}><p>{label}를 불러오지 못했습니다. 위 오류 안내를 확인하세요.</p><Button variant="outline" onClick={onReload}>경로 다시 조회</Button></div> : !hierarchy.data?.items.length ? <p id={`${id}-status`} role="status">선택한 업로드에서 조회된 경로가 없습니다.</p> : <>
      <p id={`${id}-status`} role="status">{searching ? `검색 ${matches.length.toLocaleString()}개 / 전체 ${hierarchy.data.items.length.toLocaleString()}개` : `경로를 입력하면 전체 ${hierarchy.data.items.length.toLocaleString()}개에서 검색합니다.`}{!selected && searching && ' · 목록에서 경로를 선택하세요.'}</p>
      {selected && <p className="selected-path"><Check size={15} aria-hidden="true" /><span>선택됨 · {value}</span></p>}
      {open && searching && (!matches.length ? <p className="path-no-match">검색 결과가 없습니다. 경로 일부 또는 공백으로 구분한 단어를 입력하세요.</p> : <>
        <ul className="path-results" id={`${id}-results`} role="listbox" aria-label={`${label} 검색 결과`} onKeyDown={event => { const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button')); const index = buttons.indexOf(document.activeElement as HTMLButtonElement); if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus() } if (event.key === 'Escape') { root.current?.querySelector<HTMLInputElement>('input')?.focus(); setOpen(false) } }}>{visible.map(row => <li role="presentation" key={row.path}><button role="option" type="button" disabled={disabled} aria-selected={row.path === value} onClick={() => choose(row.path)}>{row.path}</button></li>)}</ul>
        {matches.length > pageSize && <div className="path-pages"><Button variant="outline" disabled={disabled || !currentPage} aria-label={`${label} 이전 검색 결과`} onClick={() => setPage(currentPage - 1)}>이전</Button><small>{start + 1}–{Math.min(start + pageSize, matches.length)} / {matches.length.toLocaleString()}</small><Button variant="outline" disabled={disabled || currentPage === lastPage} aria-label={`${label} 다음 검색 결과`} onClick={() => setPage(currentPage + 1)}>다음</Button></div>}
      </>)}
    </>}
  </section>
}
