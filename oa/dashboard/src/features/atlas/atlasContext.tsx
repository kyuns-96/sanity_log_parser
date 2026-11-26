import type { ProcessDefinition } from '@/features/access/accessApi'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { useDashboardNavigation } from '@/lib/dashboardMode'
import { appUrl } from '@/lib/appUrl'

export type AtlasScope = { projectId: string; blockId: string; versionId: string }
export type AtlasPor = { revision: number; version_id: string | null; assigned_by?: { id?: string; name?: string } | null; assigned_at?: string | null }
export type AtlasVersion = { id: string; project_id: string; block_id: string; source_namespace: string; net_ver: string; revision: string; layout: 'PRE' | 'POST'; eco_num?: string | null; ecos: (string | null)[]; declared_ecos?: (string | null)[]; source_pre_id: string | null; previous_version_id?: string | null; area_id?: string | null }
export type AtlasBlock = { id: string; name: string; versions: AtlasVersion[]; area_id?: string | null; por?: AtlasPor }
export type AtlasProject = { id: string; process?: string | null; status?: 'active' | 'completed'; name: string; description?: string; can_write: boolean; blocks: AtlasBlock[]; area_id?: string | null }
export type UnboundArea = { id: string; project_id?: string | null; block_id?: string | null; payload?: Record<string, unknown>; name?: string }
export type AtlasCatalog = { processes?: ProcessDefinition[]; product: string; local_mode: boolean; can_manage_catalog: boolean; area_enabled: boolean; area_status?: string; area_error?: string; projects: AtlasProject[]; unbound_area?: { projects: UnboundArea[]; blocks: UnboundArea[]; versions: UnboundArea[] }; bindable_area?: { projects: UnboundArea[]; blocks: UnboundArea[]; versions: UnboundArea[] } }
export type AreaScope = { project_id: string; block_id: string | null; version_id: string | null; area_project_id: string; area_block_id: string | null; area_version_id: string | null }
export type AtlasContextValue = {
  demo: boolean; catalog: AtlasCatalog | null; scope: AtlasScope; routeSearch: string; loading: boolean; error: string;
  project?: AtlasProject; block?: AtlasBlock; version?: AtlasVersion; netVer: string; areaScope: AreaScope | null;
  reload: () => Promise<void>; selectScope: (scope: AtlasScope) => Promise<void>; selectNetVer: (netVer: string) => Promise<void>; href: (path: string) => string;
  replaceRoute: (href: string) => void;
}
const AtlasContext = createContext<AtlasContextValue | null>(null)
export const useAtlas = () => useContext(AtlasContext)

export function AtlasProvider({ routeSearch, replaceRoute, children, demo = false }: { routeSearch: string; replaceRoute: (href: string) => void; children: ReactNode; demo?: boolean }) {
  const auth = useDashboardAuth(true)
  const navigate = useDashboardNavigation()
  const [catalog, setCatalog] = useState<AtlasCatalog | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [netSelection, setNetSelection] = useState({ projectId: '', blockId: '', netVer: '' })
  const catalogRequest = useRef<AbortController | null>(null)
  const sessionKey = auth.session && (auth.session.auth_method === 'local_token' || auth.session.authorization_ready === true)
    ? `${auth.session.auth_method}:${auth.session.user_id}` : ''
  const reload = useCallback(async () => {
    catalogRequest.current?.abort()
    if (!sessionKey) { setCatalog(null); setLoading(false); setError(''); return }
    const controller = new AbortController()
    catalogRequest.current = controller
    setLoading(true)
    try {
      const value = await auth.request<AtlasCatalog>('/api/atlas/catalog', undefined, false, controller.signal)
      if (!controller.signal.aborted) { setCatalog(value); setError('') }
    } catch (cause) {
      if (!controller.signal.aborted) { setCatalog(null); setError(cause instanceof Error ? cause.message : '공통 카탈로그를 불러오지 못했습니다.'); throw cause }
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }, [auth.request, sessionKey])
  useEffect(() => {
    setCatalog(null)
    void reload().catch(() => undefined)
    return () => catalogRequest.current?.abort()
  }, [reload])
  const params = new URLSearchParams(routeSearch)
  const scope = { projectId: params.get('project') || '', blockId: params.get('block') || '', versionId: params.get('version') || '' }
  const project = catalog?.projects.find(item => item.id === scope.projectId)
  const block = project?.blocks.find(item => item.id === scope.blockId)
  const version = block?.versions.find(item => item.id === scope.versionId)
  const netVer = version?.net_ver ?? (netSelection.projectId === scope.projectId && netSelection.blockId === scope.blockId && block?.versions.some(item => item.net_ver === netSelection.netVer) ? netSelection.netVer : '')
  useEffect(() => {
    if (version) setNetSelection({ projectId: scope.projectId, blockId: scope.blockId, netVer: version.net_ver })
  }, [scope.projectId, scope.blockId, version?.id, version?.net_ver])
  const areaScope = useMemo<AreaScope | null>(() => {
    if (!project?.area_id || scope.blockId && !block?.area_id || scope.versionId && !version?.area_id) return null
    return { project_id: project.id, block_id: block?.id || null, version_id: version?.id || null, area_project_id: project.area_id, area_block_id: block?.area_id || null, area_version_id: version?.area_id || null }
  }, [project, block, version, scope.blockId, scope.versionId])
  const href = useCallback((path: string) => {
    const target = new URL(appUrl(path), location.origin)
    if (demo) target.searchParams.set('demo', '1')
    for (const [key, value] of Object.entries({ project: scope.projectId, block: scope.blockId, version: scope.versionId })) {
      if (value) target.searchParams.set(key, value); else target.searchParams.delete(key)
    }
    return target.pathname + target.search
  }, [scope.projectId, scope.blockId, scope.versionId, demo])
  const selectScope = useCallback(async (next: AtlasScope) => {
    if (next.projectId !== scope.projectId || next.blockId !== scope.blockId) setNetSelection({ projectId: '', blockId: '', netVer: '' })
    const target = new URL(location.href)
    for (const [key, value] of Object.entries({ project: next.projectId, block: next.blockId, version: next.versionId })) {
      if (value) target.searchParams.set(key, value); else target.searchParams.delete(key)
    }
    for (const key of ['review', 'item', 'eco', 'snapshot']) target.searchParams.delete(key)
    await navigate(target.pathname + target.search, auth.session?.user_id)
  }, [navigate, auth.session?.user_id, scope.projectId, scope.blockId])
  const selectNetVer = useCallback(async (next: string) => {
    setNetSelection({ projectId: scope.projectId, blockId: scope.blockId, netVer: next })
    await selectScope({ projectId: scope.projectId, blockId: scope.blockId, versionId: '' })
  }, [selectScope, scope.projectId, scope.blockId])
  return <AtlasContext value={{ demo, catalog, scope, routeSearch, project, block, version, netVer, areaScope, loading, error, reload, selectScope, selectNetVer, href, replaceRoute }}>{children}</AtlasContext>
}
