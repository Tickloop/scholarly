import { useTheme } from 'next-themes'

import { MoonIcon } from '@/components/animate-ui/icons/moon'
import { SunIcon } from '@/components/animate-ui/icons/sun'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={isDark ? 'Use light theme' : 'Use dark theme'}
          onClick={() => setTheme(isDark ? 'light' : 'dark')}
        >
          {isDark ? (
            <MoonIcon animateOnHover className="size-4" />
          ) : (
            <SunIcon animateOnHover className="size-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">
        {isDark ? 'Use light theme' : 'Use dark theme'}
      </TooltipContent>
    </Tooltip>
  )
}
