'use client'

import { Dialog } from 'radix-ui'
import { PhoneOff } from 'lucide-react'

import { cn } from '@/lib/utils'

export function EndInterviewControl({
  onEnd,
  className,
}: {
  onEnd: () => void
  className?: string
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          aria-label="End interview"
          className={cn(
            'px-glass-pill flex items-center gap-2 px-3.5 py-2 text-xs font-semibold text-px-fg',
            'border-px-danger-line hover:bg-px-danger-bg hover:text-px-danger transition-colors',
            className,
          )}
        >
          <PhoneOff className="size-3.5" />
          <span className="hidden sm:inline">End interview</span>
          <span className="sm:hidden">End</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className={cn(
            'px-glass-strong fixed left-1/2 top-1/2 z-[101] w-[min(420px,92vw)] -translate-x-1/2 -translate-y-1/2',
            'rounded-2xl border border-px-hairline p-6 text-center shadow-[var(--px-shadow-lg)]',
          )}
        >
          <Dialog.Title className="font-serif text-xl text-px-fg">End the interview?</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-px-fg-3">
            You won&apos;t be able to rejoin once the interview ends.
          </Dialog.Description>
          <div className="mt-6 flex justify-center gap-3">
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded-lg border border-px-hairline px-4 py-2 text-sm font-medium text-px-fg hover:bg-px-surface-2"
              >
                Cancel
              </button>
            </Dialog.Close>
            <Dialog.Close asChild>
              <button
                type="button"
                onClick={onEnd}
                className="rounded-lg bg-px-danger px-5 py-2 text-sm font-semibold text-white hover:opacity-90"
              >
                End
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
