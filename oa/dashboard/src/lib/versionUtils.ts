export interface VersionLabelInput {
  net_ver?: string | null
  revision?: string | null
  layout?: string | null
  eco_num?: string | null
}

export interface RevisionGroup<T> {
  key: string
  label: string
  versions: T[]
}

export function formatVersionLabel(version: VersionLabelInput, separator = ' · '): string {
  return [version.net_ver, version.revision, version.layout, version.eco_num]
    .filter(Boolean)
    .join(separator)
}

export function formatRevisionLabel(
  version: Pick<VersionLabelInput, 'revision' | 'layout' | 'eco_num'>,
  separator = ' · ',
): string {
  return [version.revision, version.layout, version.eco_num]
    .filter(Boolean)
    .join(separator)
}

export function revisionGroupKey(version: Pick<VersionLabelInput, 'revision' | 'layout'>): string {
  return `${version.revision || ''}\0${version.layout || ''}`
}

export function groupVersionsByRevision<T extends { revision: string; layout: string }>(versions: T[]): RevisionGroup<T>[] {
  const map = new Map<string, RevisionGroup<T>>()
  for (const version of versions) {
    const key = revisionGroupKey(version)
    const existing = map.get(key)
    if (existing) existing.versions.push(version)
    else map.set(key, { key, label: `${version.revision} · ${version.layout}`, versions: [version] })
  }
  return [...map.values()]
}

export function pickPreferredVersion<T extends { id: string }>(
  versions: T[],
  porVersionId?: string | null,
): T | undefined {
  if (porVersionId) {
    const por = versions.find(version => version.id === porVersionId)
    if (por) return por
  }
  return versions.length === 1 ? versions[0] : undefined
}

export function formatEcoOptionLabel(ecos: string[], isPor = false): string {
  return `${ecos.length ? ecos.join(', ') : 'ECO 미기록'}${isPor ? ' (PoR)' : ''}`
}
