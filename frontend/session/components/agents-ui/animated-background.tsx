'use client'

import { type CSSProperties } from 'react'
import { cn } from '@/lib/utils'

// Cool-light ambient blobs (sky / lavender / mint / blush). Drift + reduced-motion
// freeze are handled by the .px-bg-blob class in globals.css.
const BLOBS: { color: string; style: CSSProperties }[] = [
  { color: '#cfe0ff', style: { width: '46vw', height: '46vw', left: '-10vw', top: '-12vh' } },
  { color: '#ddd4ff', style: { width: '42vw', height: '42vw', right: '-8vw', top: '6vh', animationDelay: '-4s' } },
  { color: '#d4f0e6', style: { width: '50vw', height: '50vw', left: '15vw', bottom: '-20vh', animationDelay: '-8s' } },
  { color: '#ffe0ec', style: { width: '34vw', height: '34vw', right: '12vw', bottom: '-10vh', animationDelay: '-2s' } },
]

/** Decorative drifting ambient background. Mounted once behind all content. */
export function AnimatedBackground({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn('px-app-base pointer-events-none fixed inset-0 -z-10 overflow-hidden', className)}>
      {BLOBS.map((b, i) => (
        <span key={i} className="px-bg-blob" style={{ background: b.color, ...b.style }} />
      ))}
    </div>
  )
}
