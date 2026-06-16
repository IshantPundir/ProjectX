'use client'

import type { ReactNode } from 'react'

import { Button } from '@/components/px'
import { BrandMark } from '@/components/interview/BrandMark'
import { useFullscreenLock } from '@/hooks/use-fullscreen-lock'

/**
 * Blocking fullscreen lock for the candidate pre-check. Children always render,
 * but whenever the page is not fullscreen + visible, a full-page gate covers them
 * so the candidate cannot read or interact with the pre-check until they enter
 * fullscreen. The gate re-appears if they exit fullscreen, minimize, or switch
 * tabs. Fullscreen can only be requested from a user gesture, so entry is the
 * candidate&rsquo;s button click. Devtools are deterred separately + site-wide by
 * DevtoolsShield.
 */
export function FullscreenLockGate({ children }: { children: ReactNode }) {
  const { locked, enterFullscreen } = useFullscreenLock()
  return (
    <>
      {/* While the gate is up, the pre-check behind it is `inert` so keyboard
          users can't Tab into it — the visual block is a real interaction block.
          `display: contents` keeps the wrapper layout-neutral. */}
      <div className="contents" inert={!locked ? true : undefined}>
        {children}
      </div>
      {!locked && (
        <div
          role="alertdialog"
          aria-modal="true"
          aria-label="Fullscreen required"
          className="fixed inset-0 z-[90] grid place-items-center bg-black/80 p-6 text-center backdrop-blur-xl"
        >
          <div className="px-glass-strong flex max-w-md flex-col items-center gap-5 rounded-2xl px-8 py-10">
            <BrandMark variant="mark" className="h-9 w-9" />
            <div>
              <h2 className="px-serif text-[22px] font-normal text-px-fg">
                This is a proctored screening
              </h2>
              <p className="mt-2 text-[14px] leading-relaxed text-px-fg-3">
                To keep things fair, the screening runs in fullscreen. Enter fullscreen to
                continue &mdash; if you leave fullscreen, minimize, or switch tabs, you&rsquo;ll
                return here.
              </p>
            </div>
            <Button size="lg" autoFocus onClick={enterFullscreen} className="w-full sm:w-auto">
              Enter fullscreen to begin
            </Button>
          </div>
        </div>
      )}
    </>
  )
}
