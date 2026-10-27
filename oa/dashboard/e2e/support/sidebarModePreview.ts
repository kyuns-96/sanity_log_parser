// Synthetic browser/server fixture for mode navigation. No company SDK, DB, or model.
import { makeWorkspaceDemo } from '../../src/workspaceDemo'
import { WorkspaceError } from '../../src/workspaceApi'
import type { WorkspaceSession } from '../../src/workspaceApi'

export class SidebarModePreview {
  admin = true
  readable = true
  calls: string[] = []
  writes: string[] = []
  private workspace = makeWorkspaceDemo()
  private revision = 3
  private projects = ['Atlas', 'Boreal', 'Cygnus'].map(name => ({ id: `demo-${name.toLowerCase()}`, name, description: `${name} 합성 프로젝트` }))
  private blocks = this.projects.flatMap(project => ['CPU', 'GPU', 'DDR'].map(name => ({ id: `${project.id}-${name.toLowerCase()}`, project_id: project.id, name })))
  private requests = [{ request_id: 'synthetic-request-gpu', user_id: 'user-05', role: 'OWNER', project_id: 'demo-atlas', block_id: 'demo-atlas-gpu', reason: 'GPU 블록 검사와 로그 검토를 담당합니다.', status: 'PENDING', created_at: '2026-10-27T00:00:00Z' }]

  async request(path: string, method = 'GET', body?: Record<string, unknown>): Promise<unknown> {
    const url = new URL(path, 'http://synthetic.test')
    this.calls.push(`${method} ${url.pathname}`)
    if (method !== 'GET') this.writes.push(`${method} ${url.pathname}`)
    const session = await this.workspace<WorkspaceSession>('/session')
    const user = session.user
    const grants = this.readable ? [{ grant_id: 'synthetic-owner-cpu', user_id: user.id, role: 'OWNER', project_id: 'demo-atlas', block_id: 'demo-atlas-cpu' }] : []
    if (url.pathname === '/api/auth-config') return { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' }
    if (url.pathname === '/api/session') return { user_id: user.id, reviewer: user.name, auth_method: 'sso', authorization_ready: true, is_admin: this.admin, can_read_business: this.readable, can_receive: false, access_mode: 'postgresql', writable: this.readable, notice: '' }
    if (url.pathname === '/api/access/catalog') return { projects: this.projects, blocks: this.blocks }
    if (url.pathname === '/api/access/me') return { user_id: user.id, is_admin: this.admin, revision: this.revision, grants, requests: [] }
    if (url.pathname.startsWith('/api/admin/')) {
      if (!this.admin) throw new WorkspaceError('FORBIDDEN')
      if (url.pathname === '/api/admin/access') return { revision: this.revision, grants, requests: this.requests, users: [{ user_id: user.id, display_name: user.name }, { user_id: 'user-05', display_name: '김지안' }] }
      if (method === 'POST' && body) {
        if (url.pathname === '/api/admin/projects') this.projects.push({ id: String(body.project_id), name: String(body.name), description: String(body.description || '') })
        else if (url.pathname.endsWith('/blocks')) this.blocks.push({ id: String(body.block_id), name: String(body.name), project_id: url.pathname.split('/')[4] })
        else if (url.pathname.endsWith('/decision')) {
          const request = this.requests.find(request => request.request_id === url.pathname.split('/')[4])
          if (!request) throw new WorkspaceError('NOT_FOUND')
          request.status = body.decision === 'APPROVE' ? 'APPROVED' : 'REJECTED'
        }
        this.revision++
        return { status: 'SAVED' }
      }
    }
    if (url.pathname === '/api/ai/credentials') return { enabled: false }
    if (url.pathname.startsWith('/api/workbench/')) {
      const relative = url.pathname.slice('/api/workbench'.length) + url.search
      if (url.pathname === '/api/workbench/session') return { ...session, environment: 'PRODUCTION_SSO', is_admin: this.admin }
      if (!this.readable) {
        if (url.pathname === '/api/workbench/overview') return { user, projects: [], data_origin: 'PRODUCTION' }
        throw new WorkspaceError('FORBIDDEN')
      }
      if (/\/(intake|submissions)$/.test(url.pathname)) return { items: [], total: 0, can_confirm: true }
      const value = await this.workspace<Record<string, unknown>>(relative, body ? { body } : undefined)
      return url.pathname === '/api/workbench/overview' ? { ...value, data_origin: 'PRODUCTION' } : value
    }
    throw new WorkspaceError('NOT_FOUND')
  }
}
