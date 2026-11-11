import { ArrowUpRight } from 'lucide-react'
import { appUrl } from '../appUrl'

/** Explicit demos leave synthetic data behind when opening the real product. */
export function ModuleNavigation({ production = true }: { production?: boolean }) {
  if (production) return null
  return <nav className="module-navigation" aria-label="실제 서비스">
    <a href={appUrl('/')}><ArrowUpRight size={16} /><span>Design Atlas 실제 서비스</span></a>
  </nav>
}
