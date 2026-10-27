import { useSyncExternalStore } from 'react'
import { Check, Monitor, Moon, Sun } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { getColorMode, setColorMode, subscribeColorMode } from '../colorMode'
import type { ColorMode } from '../colorMode'

const choices = [
  { value: 'system', label: '시스템 설정', icon: Monitor },
  { value: 'light', label: '라이트 모드', icon: Sun },
  { value: 'dark', label: '다크 모드', icon: Moon },
] as const

export function ColorModeControl() {
  const { preference, resolved } = useSyncExternalStore(subscribeColorMode, getColorMode)
  const Icon = preference === 'system' ? Monitor : resolved === 'dark' ? Moon : Sun
  return <DropdownMenu.Root modal={false}>
    <DropdownMenu.Trigger asChild>
      <button type="button" className="color-mode-trigger" aria-label="화면 테마" title={`화면 테마: ${choices.find(choice => choice.value === preference)!.label}`}>
        <Icon size={17} aria-hidden="true" />
      </button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content className="color-mode-menu" align="end" sideOffset={7} collisionPadding={12}>
        <DropdownMenu.Label className="color-mode-label">화면 테마</DropdownMenu.Label>
        <DropdownMenu.RadioGroup value={preference} onValueChange={value => setColorMode(value as ColorMode)}>
          {choices.map(({ value, label, icon: ChoiceIcon }) => <DropdownMenu.RadioItem className="color-mode-option" key={value} value={value}>
            <ChoiceIcon size={16} aria-hidden="true" /><span>{label}</span>
            <DropdownMenu.ItemIndicator className="color-mode-selected"><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator>
          </DropdownMenu.RadioItem>)}
        </DropdownMenu.RadioGroup>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>
}
