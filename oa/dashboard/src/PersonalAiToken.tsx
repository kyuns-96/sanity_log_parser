import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'
import ConnectionCredentials from './ConnectionCredentials'
import './personal-ai-token.css'

export default function PersonalAiToken() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  return <>
    <Button variant="outline" size="sm" className="personal-ai-token-trigger" aria-label="개인 AI 토큰" title="개인 AI 토큰" onClick={() => setOpen(true)}><KeyRound size={14} /><span>개인 AI 토큰</span></Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
      <DialogContent className="personal-ai-token-dialog">
        <DialogHeader><DialogTitle>개인 AI 토큰</DialogTitle><DialogDescription>연결별로 발급받은 개인 토큰·API 키를 등록합니다. 인증값은 암호화해 저장하며 다시 표시하지 않습니다.</DialogDescription></DialogHeader>
        {open && <ConnectionCredentials onBusyChange={setBusy} />}
      </DialogContent>
    </Dialog>
  </>
}
