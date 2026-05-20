'use client'

import { Button } from '@/components/ui/button'

export function FullscreenGraceOverlay({
  secondsLeft,
  onReturn,
}: {
  secondsLeft: number
  onReturn: () => void
}) {
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-black/60 backdrop-blur-xl">
      <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10 text-center">
        <h2 className="font-serif text-2xl text-px-fg">Return to fullscreen to continue</h2>
        <p className="mt-3 text-sm text-px-fg-3">
          This interview must stay in fullscreen. It will end in{' '}
          <span className="font-mono font-bold text-px-danger">{Math.max(secondsLeft, 0)}s</span>{' '}
          if you don&apos;t return.
        </p>
        <Button
          size="lg"
          onClick={onReturn}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          Return to fullscreen
        </Button>
      </div>
    </div>
  )
}
