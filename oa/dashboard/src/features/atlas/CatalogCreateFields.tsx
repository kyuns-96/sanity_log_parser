import type { ProcessDefinition } from '@/features/access/accessApi'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import ProjectProcessField from './ProjectProcessField'

export function formatCatalogAreaNotice(areaPrepared?: boolean, mode: 'dialog' | 'admin' = 'dialog'): string {
  if (areaPrepared === false) {
    return mode === 'dialog'
      ? ' (Area/Power 자동 연결은 대기 중입니다. 프로젝트 비교 화면에서 다시 연결해 주세요.)'
      : ' (Area/Power 자동 연결은 대기 중입니다.)'
  }
  if (areaPrepared === true && mode === 'dialog') {
    return ' (Area/Power 범위도 함께 연결했습니다.)'
  }
  return ''
}

export interface ProjectCreateFieldsProps {
  name: string
  process: string
  description: string
  processes: ProcessDefinition[]
  disabled?: boolean
  autoFocus?: boolean
  nameHint?: string
  api: <T>(path: string, body?: Record<string, unknown>) => Promise<T>
  reload: () => Promise<void>
  onNameChange: (value: string) => void
  onProcessChange: (value: string) => void
  onDescriptionChange: (value: string) => void
}

export function ProjectCreateFields({
  name,
  process,
  description,
  processes,
  disabled = false,
  autoFocus = false,
  nameHint = '같은 이름은 기존 프로젝트를 사용합니다. 앞뒤 공백만 제거하고 대소문자와 내부 공백은 구분합니다.',
  api,
  reload,
  onNameChange,
  onProcessChange,
  onDescriptionChange,
}: ProjectCreateFieldsProps) {
  return (
    <>
      <label>
        프로젝트 이름
        <Input
          aria-label="프로젝트 이름"
          autoFocus={autoFocus}
          value={name}
          required
          maxLength={100}
          disabled={disabled}
          onChange={event => onNameChange(event.target.value)}
        />
        <small>{nameHint}</small>
      </label>
      <ProjectProcessField
        processes={processes}
        value={process}
        onChange={onProcessChange}
        api={api}
        reload={reload}
        disabled={disabled}
      />
      <label>
        설명
        <Textarea
          aria-label="설명"
          value={description}
          maxLength={1000}
          disabled={disabled}
          rows={2}
          onChange={event => onDescriptionChange(event.target.value)}
        />
      </label>
    </>
  )
}

export interface BlockCreateFieldsProps {
  name: string
  disabled?: boolean
  autoFocus?: boolean
  projectName?: string
  projects?: { id: string; name: string }[]
  selectedProjectId?: string
  nameHint?: string
  onProjectChange?: (projectId: string) => void
  onNameChange: (value: string) => void
}

export function BlockCreateFields({
  name,
  disabled = false,
  autoFocus = false,
  projectName,
  projects,
  selectedProjectId = '',
  nameHint = '선택한 프로젝트에 같은 이름의 블록이 있으면 해당 블록을 사용합니다.',
  onProjectChange,
  onNameChange,
}: BlockCreateFieldsProps) {
  return (
    <>
      {projects && onProjectChange ? (
        <label>
          프로젝트
          <select
            className="studio-native-select"
            aria-label="프로젝트"
            required
            value={selectedProjectId}
            disabled={disabled || !projects.length}
            onChange={event => onProjectChange(event.target.value)}
          >
            <option value="">프로젝트를 선택하세요</option>
            {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        </label>
      ) : projectName !== undefined && (
        <p>프로젝트: {projectName}</p>
      )}
      <label>
        블록 이름
        <Input
          aria-label="블록 이름"
          autoFocus={autoFocus}
          value={name}
          required
          maxLength={100}
          disabled={disabled}
          onChange={event => onNameChange(event.target.value)}
        />
        <small>{nameHint}</small>
      </label>
    </>
  )
}
