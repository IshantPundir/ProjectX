// app/interview/[token]/StageTransition.tsx
'use client'

import type { ReactNode } from 'react'
import { AnimatePresence, motion, type Variants } from 'motion/react'

import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

// Full motion: incoming scales up + de-blurs + fades in; outgoing scales down +
// blurs + fades out, drifting up. Exit is shorter than enter (responsive feel).
// Transform/opacity/filter only — no layout reflow.
const FULL: Variants = {
  enter: { opacity: 0, scale: 1.04, filter: 'blur(8px)', y: 12 },
  center: {
    opacity: 1,
    scale: 1,
    filter: 'blur(0px)',
    y: 0,
    transition: { type: 'spring', stiffness: 240, damping: 28, opacity: { duration: 0.4 } },
  },
  exit: {
    opacity: 0,
    scale: 0.94,
    filter: 'blur(6px)',
    y: -12,
    transition: { duration: 0.28, ease: 'easeIn' },
  },
}

const REDUCED: Variants = {
  enter: { opacity: 0 },
  center: { opacity: 1, transition: { duration: 0.15 } },
  exit: { opacity: 0, transition: { duration: 0.1 } },
}

export function StageTransition({
  stageKey,
  children,
}: {
  stageKey: string
  children: ReactNode
}) {
  const reduced = usePrefersReducedMotion()
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={stageKey}
        variants={reduced ? REDUCED : FULL}
        initial="enter"
        animate="center"
        exit="exit"
        className="w-full"
      >
        {children}
      </motion.div>
    </AnimatePresence>
  )
}
