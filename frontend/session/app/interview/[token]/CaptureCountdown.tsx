// app/interview/[token]/CaptureCountdown.tsx
'use client'

import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'

import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

const TICK_MS = 1000

/**
 * Glassmorphic capture countdown shown over the camera when the candidate starts
 * the interview. Counts `seconds`->1 then fires onComplete (capture point). If
 * `unstable` flips true mid-count (face lost/multiple, fullscreen exit, tab
 * switch, focus loss -- the caller computes it), it aborts via onAbort and never
 * completes. Both callbacks fire at most once.
 */
export function CaptureCountdown({
  seconds = 3,
  unstable,
  onComplete,
  onAbort,
}: {
  seconds?: number
  unstable: boolean
  onComplete: () => void
  onAbort: () => void
}) {
  const [n, setN] = useState(seconds)
  const reduced = usePrefersReducedMotion()
  const settled = useRef(false)

  // Abort the instant the environment becomes unstable.
  useEffect(() => {
    if (unstable && !settled.current) {
      settled.current = true
      onAbort()
    }
  }, [unstable, onAbort])

  // Tick down; complete after the last second.
  useEffect(() => {
    if (settled.current) return
    if (n <= 0) {
      settled.current = true
      onComplete()
      return
    }
    const t = window.setTimeout(() => setN((v) => v - 1), TICK_MS)
    return () => window.clearTimeout(t)
  }, [n, onComplete])

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`Capturing in ${n}`}
      className="absolute inset-0 z-[25] grid place-items-center bg-black/45 backdrop-blur-md"
    >
      <div className="flex flex-col items-center gap-5">
        <div className="relative grid size-40 place-items-center">
          <span
            className="absolute inset-0 rounded-full border border-white/25 bg-white/10 backdrop-blur-xl"
            style={{ boxShadow: '0 8px 40px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.4)' }}
          />
          <span
            aria-hidden
            className="absolute inset-0 rounded-full border-2"
            style={{ borderColor: 'var(--px-accent)', opacity: 0.7 }}
          />
          <AnimatePresence mode="popLayout">
            <motion.span
              key={n}
              initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.5 }}
              animate={reduced ? { opacity: 1 } : { opacity: 1, scale: 1 }}
              exit={reduced ? { opacity: 0 } : { opacity: 0, scale: 1.6 }}
              transition={{ duration: 0.35, ease: 'easeOut' }}
              className="px-serif text-[72px] font-normal leading-none text-white"
            >
              {n}
            </motion.span>
          </AnimatePresence>
        </div>
        <p className="text-[14px] font-medium text-white/85">Hold still -- capturing your photo</p>
      </div>
    </div>
  )
}
