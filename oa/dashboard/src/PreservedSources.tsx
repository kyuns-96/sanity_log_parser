import './preserved-sources.css'

export type PreservedSource = {
  id: string; path: string; role: 'canonical_companion' | 'constraint_input';
  binding: { kind: 'unit' | 'execution'; review_unit_id?: string; anchor_unit_id?: string };
  size_bytes: number; sha256: string; unit_key: string; execution_id: string;
  storage_status: 'PRESERVED'; content_available_to_ai: false;
}

const roles = { canonical_companion: '원천 보고서·카탈로그', constraint_input: '분석 입력 제약' }

export default function PreservedSources({ files }: { files?: PreservedSource[] }) {
  if (!files?.length) return null
  return <details className="ws-panel ws-preserved-sources" aria-label="보존 근거 파일">
    <summary><strong>보존 근거 파일 {files.length}개</strong></summary>
    <p>원본은 VWP에 보관됩니다. AI에는 아래 목록과 귀속 정보가 전달되며 파일 본문은 아직 전달되지 않습니다.</p>
    <div className="ws-table-scroll"><table><thead><tr><th>파일·역할</th><th>적용 대상</th><th>크기·무결성</th></tr></thead><tbody>
      {files.map(file => <tr key={file.id}>
        <td>{file.path}<small>{roles[file.role]}</small></td>
        <td>{file.binding.kind === 'unit' ? '검사 단위' : '원천 실행'}<small>{file.binding.review_unit_id || file.binding.anchor_unit_id}</small></td>
        <td>{file.size_bytes.toLocaleString('ko-KR')} bytes<details><summary>SHA256 확인</summary><code>{file.sha256}</code></details></td>
      </tr>)}
    </tbody></table></div>
  </details>
}
