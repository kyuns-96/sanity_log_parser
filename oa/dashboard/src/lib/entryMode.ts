import { appPathname } from '@/lib/appUrl'

// Snapshot the browser entry route once before main.tsx rewrites /workspace to
// /?page=workbench via history.replaceState. This keeps the server API boundary
// deterministic regardless of module import or lazy-loading order.
const initialPath = typeof location !== 'undefined' ? appPathname() : ''
const initialSearch = typeof location !== 'undefined' ? location.search : ''
const initialDemo = new URLSearchParams(initialSearch).get('demo') === '1'
const productionDocument = typeof location !== 'undefined' && initialPath === '/' && !initialDemo

export const entryApplicationPath = () => initialPath
export const entrySearch = () => initialSearch
export const entryDemoEnabled = () => initialDemo
export const productionWorkbench = () => productionDocument
