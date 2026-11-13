import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { accessSchema, allRecords, allUploads, request, type AreaAccess, type Auth, type TrackingRecord, type Upload } from './api'
import { Button, Empty } from './components/workspace'
import { Comparisons } from './ComparisonViews'
import { useDashboardAuth } from '../useDashboardAuth'
import { useAtlas } from '../atlasContext'

interface WorkspaceData { access: AreaAccess; records: TrackingRecord[]; uploads: Upload[] }
export default function AreaPower() {
  const host = useDashboardAuth(true)
  const atlas = useAtlas()!
  const auth = useMemo<Auth>(() => ({ actor: host.session?.reviewer || '', request: host.request, demo: atlas.demo }), [host.session?.reviewer, host.request, atlas.demo])
  const [data, setData] = useState<WorkspaceData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<string | null>(null)
  const [generation, setGeneration] = useState(0)
  const controller = useRef<AbortController | null>(null)
  const refresh = useCallback(async () => {
    controller.current?.abort()
    if (!atlas.catalog?.area_enabled) { setData(null); setLoading(false); return }
    const current = new AbortController(); controller.current = current
    setLoading(true)
    try {
      const [access, uploads, projects, blocks, versions, snapshots, mappings, comparisons] = await Promise.all([
        request(auth, '/access', accessSchema, undefined, current.signal), allUploads(auth, current.signal),
        allRecords(auth, 'project', { latest: 'false' }, current.signal), allRecords(auth, 'block', { latest: 'false' }, current.signal),
        allRecords(auth, 'version', { latest: 'false' }, current.signal), allRecords(auth, 'snapshot', { latest: 'false' }, current.signal),
        allRecords(auth, 'mapping', { latest: 'false' }, current.signal),
        allRecords(auth, 'comparison', { latest: 'false' }, current.signal),
      ])
      if (!current.signal.aborted) { setData({ access, uploads, records: [...projects, ...blocks, ...versions, ...snapshots, ...mappings, ...comparisons] }); setError(''); setUpdated(new Date().toISOString()); setGeneration(value => value + 1) }
    } catch (cause) {
      if (!current.signal.aborted) { setData(null); setError(cause instanceof Error ? cause.message : 'Area/Power 서비스 연결을 확인하세요.'); throw cause }
    } finally { if (!current.signal.aborted) setLoading(false) }
  }, [auth, atlas.catalog?.area_enabled])
  useEffect(() => { void refresh().catch(() => undefined); return () => controller.current?.abort() }, [refresh])
  return <div className="area-power"><main id="area-power-main" tabIndex={-1}>
    <div className="page-heading"><div><span className="eyebrow">AREA / POWER · PROJECT BASELINE</span><h1>프로젝트 비교<span className="heading-dot">.</span></h1><p>저장된 프로젝트 기준과 명시한 실행으로 계층별 Area·Power 차이를 확인합니다.</p></div><div className="page-controls"><small>{updated ? `마지막 조회 ${new Date(updated).toLocaleString('ko-KR', { hour12: false })}` : atlas.demo ? '합성 자료 조회 전' : '실제 데이터 조회 전'}</small><Button variant="outline" disabled={loading || !atlas.catalog?.area_enabled} onClick={() => void refresh().catch(() => undefined)}><RefreshCw size={15} className={loading ? 'spin' : ''} />새로고침</Button></div></div>
    {!atlas.catalog?.area_enabled ? <Empty title="수치 분석 서비스가 준비되지 않았습니다">{atlas.catalog?.area_error || '운영 설정을 확인하세요. 데모로 대체하지 않습니다.'}</Empty> : loading && !data ? <div className="loading-state" role="status"><LoaderCircle className="spin" size={23} />프로젝트와 업로드 이력을 불러오는 중입니다…</div> : error && !data ? <div className="notice error" role="alert">{error}</div> : !data ? <Empty title="Area/Power 자료를 불러오지 못했습니다">연결을 확인하고 새로고침하세요. 다른 데이터로 대체하지 않습니다.</Empty> : <>
      {atlas.project ? <Comparisons key={atlas.project.id} auth={auth} access={data.access} records={data.records} uploads={data.uploads} comparisons={data.records.filter(row => row.record_type === 'comparison')} refresh={refresh} refreshVersion={generation} loading={loading} /> : <Empty title="공통 프로젝트를 선택하세요">프로젝트를 선택하면 기준 설정과 실행 비교를 시작할 수 있습니다.</Empty>}
      {error && <div className="notice error" role="alert"><strong>요청을 완료하지 못했습니다</strong><p>{error}</p></div>}
    </>}
    <footer className="page-footer"><span>Design Atlas · 프로젝트 간 Area / Power 비교</span><span>명시적 구조 대응 · 실행별 근거</span></footer>
  </main></div>
}
