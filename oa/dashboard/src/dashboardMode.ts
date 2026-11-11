import { createContext, useContext, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import type { DashboardMode } from './components/SidebarModeSwitch'
import { appPathname, appUrl } from './appUrl'

export type DashboardNavigate = (href: string, sourceUser?: string, reset?: boolean) => Promise<void>
export const DashboardNavigationContext = createContext<DashboardNavigate>(async href => { location.assign(appUrl(href)) })
export const useDashboardNavigation = () => useContext(DashboardNavigationContext)
export const DashboardSidebarContext = createContext<[boolean, Dispatch<SetStateAction<boolean>>] | null>(null)
export function useDashboardSidebarState() {
  const local = useState(false)
  return useContext(DashboardSidebarContext) ?? local
}

const returnKey = 'user_view'
const userPages: Record<string, true> = { workbench: true, welcome: true, review: true, 'area-power': true }

export function userModeUrl(canRead: boolean, current = new URL(location.href)): string {
  const fallback = appUrl(canRead ? '/?page=workbench' : '/?page=welcome')
  const value = current.searchParams.get(returnKey)
  if (!value || !value.startsWith('/?') || /[\\\u0000-\u001f\u007f]/u.test(value)) return fallback
  try {
    const url = new URL(value, current.origin)
    if (url.origin !== current.origin || appPathname(url.pathname) !== '/' || !Object.hasOwn(userPages, url.searchParams.get('page') || 'review')
      || url.searchParams.has('demo') || url.searchParams.has('flow') || url.searchParams.has('dataset')) return fallback
    if (!canRead && url.searchParams.get('page') !== 'welcome') return appUrl('/?page=welcome')
    url.searchParams.delete(returnKey)
    return appUrl(url.pathname + url.search)
  } catch { return fallback }
}

export function modeUrl(mode: DashboardMode, canRead: boolean): string {
  const current = new URL(location.href)
  if (mode === 'user') return userModeUrl(canRead, current)
  const user = new URL(current)
  user.searchParams.delete(returnKey)
  const target = new URL('/', current.origin)
  target.searchParams.set('page', 'admin')
  target.searchParams.set(returnKey, appPathname(user.pathname) + (user.search || '?page=review'))
  return appUrl(target.pathname + target.search)
}
