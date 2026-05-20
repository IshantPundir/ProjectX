'use client'

import { motion, useReducedMotion } from 'motion/react'
import type { BorderFlash } from './use-proctoring-controller'

export function ViolationBorder({ flash }: { flash: BorderFlash | null }) {
  const reduce = useReducedMotion()
  if (!flash) return null
  const color = flash.tone === 'hard' ? 'var(--px-danger)' : 'var(--px-caution)'
  return (
    <>
      <motion.div
        key={flash.key}
        aria-hidden
        className="pointer-events-none fixed inset-0 z-[60]"
        initial={{ opacity: reduce ? 0.7 : 0 }}
        animate={reduce ? { opacity: 0.7 } : { opacity: [0, 1, 0.6, 1, 0] }}
        transition={reduce ? undefined : { duration: 2.4, times: [0, 0.15, 0.5, 0.7, 1] }}
        style={{ boxShadow: `inset 0 0 0 4px ${color}, inset 0 0 48px ${color}` }}
      />
      <span role="alert" className="sr-only">
        {flash.tone === 'hard'
          ? 'Interview ending due to a monitoring violation.'
          : 'Warning: monitoring detected an interview-rule violation.'}
      </span>
    </>
  )
}
