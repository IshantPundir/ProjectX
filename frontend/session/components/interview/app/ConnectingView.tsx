// components/interview/app/ConnectingView.tsx
'use client'

import { BrandMark } from '@/components/interview/BrandMark'

/**
 * Shown while the start path connects to LiveKit (autoStart). Replaces the old
 * second "Start interview" welcome screen -- the wizard's Ready stage is the only
 * Start the candidate sees.
 */
export function ConnectingView() {
  return (
    <div className="px-cine-bg grid min-h-dvh place-items-center px-6">
      <div className="px-glass flex max-w-sm flex-col items-center gap-4 rounded-2xl px-8 py-10 text-center">
        <BrandMark variant="mark" className="h-9 w-9" />
        <div
          className="size-6 animate-spin rounded-full border-2 border-px-hairline border-t-px-accent"
          aria-hidden
        />
        <p className="text-[15px] font-medium text-px-fg" role="status" aria-live="polite">
          Connecting you to your interview...
        </p>
        <p className="text-[13px] text-px-fg-3">Arjun will be with you in a moment.</p>
      </div>
    </div>
  )
}
