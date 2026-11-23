/** Code-server strips /proxy/<port> (or /absproxy/<port>) before serving OA. */
const proxyPrefix = typeof location === 'undefined' ? '' : location.pathname.match(/^(.*\/(?:absproxy|proxy)\/\d+)(?=\/|$)/)?.[1] ?? ''
export const appRoot = `${proxyPrefix}/`

export function appUrl(path: string): string {
  if (!path.startsWith('/') || path.startsWith('//')) return path
  if (proxyPrefix && (path === proxyPrefix || path.startsWith(`${proxyPrefix}/`))) return path
  return `${proxyPrefix}${path}`
}

export function appPathname(pathname = location.pathname): string {
  return proxyPrefix && (pathname === proxyPrefix || pathname.startsWith(`${proxyPrefix}/`)) ? pathname.slice(proxyPrefix.length) || '/' : pathname
}
