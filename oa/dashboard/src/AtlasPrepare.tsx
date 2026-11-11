import { useState } from 'react'
import { useAtlas } from './atlasContext'

export default function AtlasPrepare() {
  const atlas = useAtlas()!
  return <Preparation key={`${atlas.scope.projectId}:${atlas.scope.blockId}:${atlas.scope.versionId}`} />
}
function Preparation() {
  const atlas = useAtlas()!
  const [previous, setPrevious] = useState('')
  const [error, setError] = useState('')
  const needsLineage = !!atlas.version && !Object.hasOwn(atlas.version, 'previous_version_id')
  const canWrite = atlas.project?.can_write || atlas.catalog?.can_manage_catalog
  return <div className="atlas-preparation">{needsLineage && <label>수치 버전 계보를 명시적으로 선언하세요<select value={previous} onChange={event => setPrevious(event.target.value)}><option value="">이전 버전 또는 계보 시작을 선택</option><option value="null">이 버전에서 계보 시작 (이전 버전 없음)</option>{atlas.block?.versions.filter(version => version.id !== atlas.version?.id).map(version => <option key={version.id} value={JSON.stringify(version.id)}>{version.net_ver} · {version.revision} · {version.layout}</option>)}</select><small>이 선언은 PRE→POST source 관계와 별개입니다. 이전 버전이 있다면 먼저 해당 범위의 수치 분석을 준비하세요.</small></label>}<button className="atlas-button" disabled={atlas.preparing || !canWrite || needsLineage && !previous} onClick={() => { setError(''); void atlas.prepare(needsLineage ? JSON.parse(previous) as string | null : undefined).catch(cause => setError(cause instanceof Error ? cause.message : String(cause))) }}>{atlas.preparing ? '준비 중…' : '수치 분석 준비'}</button>{!canWrite && <p>현재 프로젝트의 쓰기 권한이 필요합니다.</p>}{error && <div className="atlas-message error" role="alert">{error}<p>자동 재시도하지 않습니다. 공통 범위 새로고침으로 실제 저장 상태를 확인한 후 필요한 경우 직접 다시 준비하세요.</p></div>}</div>
}
