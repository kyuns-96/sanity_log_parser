export type MappingDraft = { baselinePath: string; currentPath: string; scope: 'node' | 'subtree' }

const headers = ['baseline_path', 'current_path', 'scope'] as const

export function mappingCsv(rows: MappingDraft[]) {
  const quote = (value: string) => /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value
  return '\uFEFF' + [headers.join(','), ...rows.map(row => [row.baselinePath, row.currentPath, row.scope].map(quote).join(','))].join('\r\n') + '\r\n'
}

export function readMappingCsv(input: string): MappingDraft[] {
  const source = input.replace(/^\uFEFF/, '')
  const rows: string[][] = []
  let row: string[] = [], cell = '', quoted = false, closed = false
  for (let index = 0; index <= source.length; index++) {
    const character = source[index]
    if (quoted) {
      if (character === undefined) throw new Error('CSV의 따옴표가 닫히지 않았습니다.')
      if (character === '"') {
        if (source[index + 1] === '"') { cell += '"'; index++ }
        else { quoted = false; closed = true }
      } else cell += character
      continue
    }
    if (character === '"') {
      if (cell || closed) throw new Error('CSV 따옴표는 셀의 처음에만 사용할 수 있습니다.')
      quoted = true
    } else if (character === ',' || character === '\r' || character === '\n' || character === undefined) {
      row.push(cell); cell = ''; closed = false
      if (character !== ',') {
        if (row.length !== 1 || row[0] !== '') rows.push(row)
        row = []
        if (character === '\r' && source[index + 1] === '\n') index++
      }
    } else {
      if (closed) throw new Error('CSV의 닫는 따옴표 뒤에는 구분자 또는 줄바꿈이 필요합니다.')
      cell += character
    }
  }
  const heading = rows.shift()
  if (!heading || heading.length !== headers.length || !headers.every(header => heading.includes(header))) throw new Error('CSV 헤더는 baseline_path,current_path,scope 세 열이어야 합니다. Export template을 사용하세요.')
  if (!rows.length) throw new Error('CSV에 대응 규칙이 없습니다. 헤더 아래에 경로와 scope를 입력하세요.')
  const before = heading.indexOf('baseline_path'), current = heading.indexOf('current_path'), scope = heading.indexOf('scope')
  return rows.map((cells, index) => {
    if (cells.length !== headers.length) throw new Error(`CSV 데이터 ${index + 1}행의 열 수가 3개가 아닙니다.`)
    if (cells[scope] !== 'node' && cells[scope] !== 'subtree') throw new Error(`CSV 데이터 ${index + 1}행의 scope는 node 또는 subtree여야 합니다.`)
    return { baselinePath: cells[before], currentPath: cells[current], scope: cells[scope] }
  })
}

export function validateMappingDraft(rows: MappingDraft[], baselinePaths: Set<string>, currentPaths: Set<string>) {
  const baseline = new Set<string>(), current = new Set<string>()
  for (const [index, row] of rows.entries()) {
    if (!baselinePaths.has(row.baselinePath)) throw new Error(`대응 ${index + 1}행의 기준 경로가 선택한 실행에 없습니다: ${row.baselinePath || '(빈 값)'}`)
    if (!currentPaths.has(row.currentPath)) throw new Error(`대응 ${index + 1}행의 현재 경로가 선택한 실행에 없습니다: ${row.currentPath || '(빈 값)'}`)
    const beforeKey = `${row.scope}\0${row.baselinePath}`, currentKey = `${row.scope}\0${row.currentPath}`
    if (baseline.has(beforeKey) || current.has(currentKey)) throw new Error(`대응 ${index + 1}행은 같은 scope의 기준 또는 현재 경로를 중복 사용합니다.`)
    baseline.add(beforeKey); current.add(currentKey)
  }
}
