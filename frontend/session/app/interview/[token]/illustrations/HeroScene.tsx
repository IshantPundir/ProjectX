// app/interview/[token]/illustrations/HeroScene.tsx
'use client'

// Calm, on-brand hero: a glowing "Arjun" orb above a soft desk horizon with a
// few drifting particles. Token-colored via the inherited --px-accent. Motion is
// CSS-only and reduced-motion-safe (the keyframes are gated in globals.css; see
// a later task). Decorative — aria-hidden.
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

export function HeroScene({ className }: { className?: string }) {
  const reduced = usePrefersReducedMotion()
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 320 320"
      className={className}
      role="presentation"
    >
      <defs>
        <radialGradient id="orbGlow" cx="50%" cy="42%" r="55%">
          <stop offset="0%" stopColor="var(--px-accent)" stopOpacity="0.9" />
          <stop offset="55%" stopColor="var(--px-accent)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--px-accent)" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="deskFade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--px-accent)" stopOpacity="0.18" />
          <stop offset="100%" stopColor="var(--px-accent)" stopOpacity="0" />
        </linearGradient>
      </defs>

      {/* glow halo */}
      <circle cx="160" cy="140" r="120" fill="url(#orbGlow)" />

      {/* orb */}
      <g className={reduced ? undefined : 'hero-orb'}>
        <circle cx="160" cy="140" r="46" fill="var(--px-surface)" stroke="var(--px-accent)" strokeWidth="2" />
        <circle cx="160" cy="140" r="14" fill="var(--px-accent)" />
        <circle cx="160" cy="140" r="70" fill="none" stroke="var(--px-accent)" strokeOpacity="0.25" strokeWidth="1.5" />
      </g>

      {/* desk horizon */}
      <rect x="40" y="232" width="240" height="60" rx="10" fill="url(#deskFade)" />
      <line x1="40" y1="232" x2="280" y2="232" stroke="var(--px-hairline-strong)" strokeWidth="1.5" />

      {/* drifting particles */}
      {[
        { cx: 96, cy: 96, r: 3 },
        { cx: 232, cy: 110, r: 2.5 },
        { cx: 210, cy: 70, r: 2 },
        { cx: 110, cy: 190, r: 2 },
      ].map((p, i) => (
        <circle
          key={i}
          cx={p.cx}
          cy={p.cy}
          r={p.r}
          fill="var(--px-accent)"
          opacity="0.5"
          className={reduced ? undefined : `hero-particle hero-particle-${i}`}
        />
      ))}
    </svg>
  )
}
