import type { WorkspaceUser } from './workspaceApi'

export const workspaceDemoUsers: WorkspaceUser[] = [
  ['이서연', 'Atlas TOP'], ['박지훈', 'Boreal TOP'], ['김도윤', 'Cygnus TOP'],
  ['정민수', '블록 담당자'], ['최유진', '블록 담당자'], ['한지민', '블록 담당자'],
  ['오서준', '블록 담당자'], ['윤지호', '블록 담당자'], ['임수빈', '블록 담당자'], ['강현우', '블록 담당자'],
].map(([name, role], index) => ({ id: `demo-user-${index + 1}`, username: `user${String(index + 1).padStart(2, '0')}`, name, role }))
