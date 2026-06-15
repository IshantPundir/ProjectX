// app/interview/[token]/illustrations/glyphs.tsx
// Bespoke line glyphs for the instruction list. All use currentColor + a
// consistent 1.5 stroke so they inherit the row's token color and stay crisp at
// any size. Decorative — the row's text label is the accessible name.
import type { SVGProps } from 'react'

const base: SVGProps<SVGSVGElement> = {
  width: 24,
  height: 24,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

/** Arjun — a friendly AI orb with a soft spark. */
export function ArjunGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="6.5" />
      <circle cx="12" cy="12" r="2.25" fill="currentColor" stroke="none" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2" opacity="0.55" />
    </svg>
  )
}

/** Quiet room — a person silhouette with a hush wave. */
export function QuietRoomGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <path d="M17 8c1.4 1.2 1.4 6.8 0 8M19.5 6c2.4 2 2.4 8 0 12" opacity="0.55" />
    </svg>
  )
}

/** Single screen — one monitor, a second crossed out. */
export function SingleScreenGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="4.5" width="12" height="9" rx="1.5" />
      <path d="M9 13.5v3M6.5 16.5h5" />
      <path d="M18 7l4 4M22 7l-4 4" opacity="0.7" />
    </svg>
  )
}

/** Proctored — a shield with a check. */
export function ShieldGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M12 2.5l7 2.5v6c0 4.2-2.9 7.4-7 9-4.1-1.6-7-4.8-7-9V5z" />
      <path d="M9 11.5l2.2 2.2L15.5 9.4" />
    </svg>
  )
}

/** One-time link — a chain link with a small clock/expiry hint. */
export function OneTimeLinkGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M9.5 14.5l5-5" />
      <path d="M7 12l-1.5 1.5a3 3 0 0 0 4.25 4.25L11 16.5" />
      <path d="M17 12l1.5-1.5a3 3 0 0 0-4.25-4.25L13 7.5" />
    </svg>
  )
}
