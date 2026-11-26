import { aiConnectionNotice, needsPersonalCredential, taskDefaultNotice } from '@/features/ai/AiModelSelect'
import type { CodexCapability } from './workspaceApi'

export interface WorkspaceAiNoticesProps {
  production: boolean
  ai?: CodexCapability
  choice?: CodexCapability
  stage: '분류' | '판정'
}

export function WorkspaceAiNotices({ production, ai, choice, stage }: WorkspaceAiNoticesProps) {
  if (!production) return null
  const personalTokenMessage = stage === '분류'
    ? '개인 AI 토큰을 사용할 수 없습니다. 화면 상단의 ‘개인 AI 토큰’에서 등록 상태와 만료일을 확인해 주세요.'
    : '화면 상단의 ‘개인 AI 토큰’에서 본인의 토큰을 등록하거나 교체해 주세요.'
  const connectionMessage = aiConnectionNotice(choice)
  const defaultMessage = taskDefaultNotice(ai, stage)

  return (
    <>
      {needsPersonalCredential(choice) && <p className="ws-notice" role="status">{personalTokenMessage}</p>}
      {connectionMessage && <p className="ws-notice" role="status">{connectionMessage}</p>}
      {defaultMessage && <p className="ws-notice" role="status">{defaultMessage}</p>}
    </>
  )
}
