export type AccessRole = 'TOP' | 'OWNER'
export type AccessProject = { id: string; name: string; description: string }
export type AccessBlock = { id: string; project_id: string; name: string }
export type AccessCatalog = { projects: AccessProject[]; blocks: AccessBlock[] }
export type AccessGrant = { grant_id: string; user_id: string; role: AccessRole; project_id: string; block_id: string | null }
export type AccessRequest = { request_id: string; user_id: string; role: AccessRole; project_id: string; block_id: string | null; reason: string; status: 'PENDING' | 'APPROVED' | 'REJECTED'; created_at: string; decided_by?: string; decision_reason?: string }
export type MyAccess = { user_id: string; is_admin: boolean; revision: number; grants: AccessGrant[]; requests: AccessRequest[] }
export type AdminAccess = { revision: number; requests: AccessRequest[]; grants: AccessGrant[]; users: { user_id: string; display_name: string }[] }
export type AccessApi = <T>(path: string, body?: Record<string, unknown>, plain?: boolean) => Promise<T>
export type AccessPage = 'welcome' | 'admin' | 'review'
export const roleLabel = (role: AccessRole) => role === 'TOP' ? 'TOP · 프로젝트 조회' : 'OWNER · 블록 검토'
export const requestStatus = { PENDING: '승인 대기', APPROVED: '승인됨', REJECTED: '거절됨' }
export const accessError = (error: unknown) => error instanceof Error ? error.message : '연결 상태를 확인하고 다시 시도해 주세요.'
export function scopeName(catalog: AccessCatalog, value: { project_id: string; block_id: string | null }): string {
  const project = catalog.projects.find(item => item.id === value.project_id)?.name || value.project_id
  return value.block_id ? `${project} / ${catalog.blocks.find(item => item.id === value.block_id && item.project_id === value.project_id)?.name || value.block_id}` : project
}
