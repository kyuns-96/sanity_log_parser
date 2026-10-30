import { Input } from './studio/components/ui/input'
import type { BatchConfig, ProcessingConfig } from './aiConnectionTypes'

export default function AIProcessingLimits({ config, busy, onChange }: { config: ProcessingConfig; busy: boolean; onChange: (value: Partial<ProcessingConfig>) => void }) {
  const batch = (value: Partial<BatchConfig>) => onChange({ batch: { ...config.batch, ...value } })
  // Keep the planning reservation in sync with BatchModel.leaf_plan().
  const classificationCap = Number.isInteger(config.batch.output_tokens) && config.batch.output_tokens > 0 && Number.isInteger(config.batch.max_records) && config.batch.max_records > 0
    ? Math.min(config.batch.max_records, Math.max(1, Math.floor(config.batch.output_tokens / 192))) : null
  return <details className="ai-processing-limits">
    <summary><span className="ai-limits-summary"><strong>처리 한도와 전체 로그 배치 설정</strong><span className="ai-limits-summary-description">요청 크기와 배치 처리 기준을 설정합니다.</span></span></summary>
    <div className="ai-limits-content">
      <div className="ai-connection-fields ai-limits-grid">
        {([['timeout', '응답 제한 시간(초)', 1, 600], ['max_input_bytes', '요청 byte 한도', 1, 67108864], ['max_output_bytes', '응답 byte 한도', 1, 67108864]] as const).map(([key, label, min, max]) => <label key={key}>{label}<Input type="number" aria-label={label} required min={min} max={max} disabled={busy} value={config[key]} onChange={event => onChange({ [key]: Number(event.target.value) })} /></label>)}
      </div>
      <div className="ai-option-row"><label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.json_mode} onChange={event => onChange({ json_mode: event.target.checked })} /><span>JSON object 응답 옵션 사용</span></label></div>
      <div className="ai-option-row"><label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.batch.enabled} onChange={event => batch({ enabled: event.target.checked })} /><span>전체 로그·그룹 판정·보존 본문 배치 사용</span></label></div>
      <p>모든 허용 모델에서 안전한 공통 한도를 입력하세요. 배치 입력은 보수적인 UTF-8 byte 계수를 사용하며 실제 모델 토큰 수와 다릅니다.</p>
      {config.batch.enabled && <>
        <div className="ai-limits-section">
          <h4>토큰 예산</h4>
          <p>모델의 입력과 출력에 사용할 한도를 정합니다.</p>
          <div className="ai-connection-fields ai-limits-grid">
            {([['context_tokens','모델 context 한도',1,10000000], ['input_tokens','배치 입력 예산',1,1000000], ['output_tokens','출력 예약',1,1000000], ['overhead_tokens','메시지 오버헤드 예약',1,1000000]] as const).map(([key,label,min,max]) => <label key={key}>{label}<Input type="number" aria-label={label} required min={min} max={max} disabled={busy} value={config.batch[key]} onChange={event => batch({ [key]: Number(event.target.value) })} /></label>)}
            <label>출력 한도 파라미터<select className="studio-native-select" aria-label="출력 한도 파라미터" disabled={busy} value={config.batch.output_parameter} onChange={event => batch({ output_parameter: event.target.value })}>{(config.api === 'responses' ? ['max_output_tokens'] : ['max_tokens','max_completion_tokens']).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          </div>
        </div>
        <div className="ai-limits-section">
          <h4>분할과 실행 한도</h4>
          <p>로그 분할 크기와 전체 작업의 실행 범위를 정합니다.</p>
          {classificationCap != null && <p className="ai-availability-note" role="status">현재 설정의 분류 배치 상한: <strong>{classificationCap.toLocaleString()}건 / 호출</strong>. 5만 항목이라면 최초 분류만 최소 {Math.ceil(50000 / classificationCap).toLocaleString()}회 호출합니다. 입력 크기에 따라 더 작게 나뉠 수 있고, 이후 통합 호출은 별도입니다. 조각별 최대 로그와 출력 예약을 함께 확인하세요.</p>}
          <p>응답 제한 시간이 짧으면 정상 생성 중에도 결과를 확인하지 못할 수 있습니다. 실제 모델·게이트웨이의 응답 시간과 허용 한도를 확인해 설정하세요. 실행 중인 작업의 연결 설정 변경은 해당 작업을 중단시킬 수 있습니다.</p>
          <div className="ai-connection-fields ai-limits-grid">
            {([['max_records','조각별 최대 로그',1,1000], ['merge_groups','통합별 최대 그룹',2,100], ['max_calls','전체 호출 한도',1,100000], ['max_rounds','통합 라운드 한도',1,32], ['deadline_seconds','전체 작업 제한 시간(초)',60,604800]] as const).map(([key,label,min,max]) => <label key={key}>{label}<Input type="number" aria-label={label} required min={min} max={max} disabled={busy} value={config.batch[key]} onChange={event => batch({ [key]: Number(event.target.value) })} /></label>)}
          </div>
        </div>
      </>}
    </div>
  </details>
}
