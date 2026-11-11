// Free Shadcn Studio button-01, installed with shadcn CLI v4.
// Source: https://shadcnstudio.com/r/new-york/button-01.json
// Adaptation: expose native button props for real application actions.
import type { ComponentProps } from 'react'
import { Button } from '../../ui/button'

const ButtonDemo = ({ children, ...props }: ComponentProps<typeof Button>) => {
  return <Button {...props}>{children}</Button>
}

export default ButtonDemo
