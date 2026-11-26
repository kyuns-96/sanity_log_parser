import { useEffect, useRef, useState, type RefObject } from 'react'
import { detailSchema, request, type Auth, type ComparisonDetail } from '../api'
import type { ChildState } from '../HierarchyBrowser'
import { referenceRows } from './hierarchyUtils'

export function useComparisonDetail(auth: Auth, refreshVersion: number, observed: string, alive: RefObject<boolean>) {
  const [comparisonId, setComparisonId] = useState('')
  const [detail, setDetail] = useState<ComparisonDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailRefresh, setDetailRefresh] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)
  const [resultSearch, setResultSearch] = useState('')
  const [resultQuery, setResultQuery] = useState({ offset: 0, search: '' })
  const [children, setChildren] = useState<Map<string, ChildState>>(() => new Map())
  const treeEpoch = useRef(0)

  useEffect(() => {
    treeEpoch.current += 1
    setChildren(new Map())
  }, [comparisonId, detailRefresh, refreshVersion, resultQuery, observed])

  useEffect(() => {
    if (!comparisonId) {
      setDetail(null)
      setDetailError('')
      setDetailLoading(false)
      return
    }
    const controller = new AbortController()
    let timer: number | undefined
    const load = async () => {
      setDetailLoading(true)
      try {
        const path = `/comparisons/${encodeURIComponent(comparisonId)}`
        let value = await request(auth, `${path}?view=summary`, detailSchema, undefined, controller.signal)
        const running = ['queued', 'pending', 'running'].includes(value.comparison.state)
        if (!running) {
          const params = new URLSearchParams(observed)
          for (const [key, entry] of Object.entries({ view: 'hierarchy', offset: String(resultQuery.offset), limit: '200', search: resultQuery.search })) {
            params.set(key, entry)
          }
          if (!resultQuery.search) params.set('parent', '')
          value = await request(auth, `${path}?${params}`, detailSchema, undefined, controller.signal)
        }
        if (controller.signal.aborted) return
        setDetail(value)
        setDetailError('')
        if (['queued', 'pending', 'running'].includes(value.comparison.state)) {
          timer = window.setTimeout(() => void load(), 2500)
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setDetail(null)
          setDetailError(cause instanceof Error ? cause.message : '비교 결과를 조회하지 못했습니다.')
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false)
      }
    }
    void load()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [auth, comparisonId, detailRefresh, refreshVersion, resultQuery, observed])

  async function loadChildren(key: string, offset: number) {
    const epoch = treeEpoch.current
    const id = comparisonId
    const update = (change: (state: ChildState) => ChildState) => setChildren(map => {
      if (epoch !== treeEpoch.current) return map
      const state = map.get(key) || { rows: [], total: 0, loading: false, error: '' }
      return new Map(map).set(key, change(state))
    })
    update(state => ({ ...state, loading: true, error: '' }))
    try {
      const params = new URLSearchParams(observed)
      for (const [name, value] of Object.entries({ view: 'hierarchy', parent: key, offset: String(offset), limit: '500' })) {
        params.set(name, value)
      }
      const value = await request(auth, `/comparisons/${encodeURIComponent(id)}?${params}`, detailSchema)
      if (!alive.current) return
      update(state => ({
        rows: [...state.rows.slice(0, offset), ...referenceRows(value)],
        total: value.result_page?.total_groups ?? 0,
        hidden: value.result_page?.hidden_groups ?? 0,
        loading: false,
        error: '',
      }))
    } catch (cause) {
      if (alive.current) {
        update(state => ({ ...state, loading: false, error: cause instanceof Error ? cause.message : '하위 instance를 조회하지 못했습니다.' }))
      }
    }
  }

  function openComparison(id: string) {
    setDetail(null)
    setDetailError('')
    setResultSearch('')
    setResultQuery({ offset: 0, search: '' })
    setComparisonId(id)
    setDetailRefresh(value => value + 1)
  }

  function closeComparison() {
    setComparisonId('')
    setDetail(null)
    setDetailError('')
  }

  return {
    comparisonId,
    detail,
    detailError,
    detailLoading,
    detailRefresh,
    setDetailRefresh,
    resultSearch,
    setResultSearch,
    resultQuery,
    setResultQuery,
    children,
    loadChildren,
    openComparison,
    closeComparison,
  }
}
