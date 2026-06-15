'use client'

import { Dialog } from 'radix-ui'
import { X } from 'lucide-react'

/**
 * Read-only disclosure of the full AIVIA consent text. Opening this dialog does
 * NOT record consent — only the Intro "I'm ready" CTA does that. radix provides
 * focus-trap + Esc-to-close for accessibility.
 */
export function ConsentDialog({ consentText }: { consentText: string }) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="text-[13px] font-medium text-px-accent underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-px-accent"
        >
          Privacy &amp; consent
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45 backdrop-blur-sm" />
        <Dialog.Content className="px-glass fixed left-1/2 top-1/2 z-50 max-h-[80vh] w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl p-6">
          <div className="mb-3 flex items-start justify-between gap-4">
            <Dialog.Title className="px-serif text-[20px] font-normal text-px-fg">
              Privacy &amp; consent
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="grid size-8 place-items-center rounded-full text-px-fg-3 hover:bg-px-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-px-accent"
              >
                <X className="size-4" aria-hidden />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">
            Full consent and privacy terms for this AI interview.
          </Dialog.Description>
          <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-px-fg-2">
            {consentText}
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
