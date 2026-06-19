import { useId } from 'react'
import type { Verdict } from '@/lib/api/reports'

interface StampConfig {
  text: string
  color: string
}

function stampConfig(verdict: Verdict): StampConfig {
  switch (verdict) {
    case 'advance':
      return { text: 'APPROVED', color: '#36d07f' }
    case 'borderline':
      return { text: 'BORDERLINE', color: '#f0b429' }
    case 'reject':
    default:
      return { text: 'REJECTED', color: '#ff6b6b' }
  }
}

export function VerdictStamp({ verdict }: { verdict: Verdict }) {
  const uid = useId()
  // SVG filter ids are document-global — namespace per-instance
  const wornId = `worn-${uid.replace(/:/g, '')}`
  const { text, color } = stampConfig(verdict)

  return (
    <svg
      width="190"
      height="95"
      viewBox="0 0 200 100"
      role="img"
      aria-label={`Verdict: ${text.charAt(0) + text.slice(1).toLowerCase()}`}
      style={{ filter: `drop-shadow(0 0 10px ${color}40)`, flex: 'none' }}
    >
      <defs>
        <filter id={wornId} x="-12%" y="-25%" width="124%" height="150%">
          <feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="2" seed="7" result="warp" />
          <feDisplacementMap in="SourceGraphic" in2="warp" scale="1.5" xChannelSelector="R" yChannelSelector="G" result="wob" />
          <feTurbulence type="fractalNoise" baseFrequency="0.55" numOctaves="2" seed="4" result="speck" />
          <feColorMatrix in="speck" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -0.45 1.06" result="mask" />
          <feComposite in="wob" in2="mask" operator="in" />
        </filter>
      </defs>
      <g filter={`url(#${wornId})`} transform="rotate(-8 100 50)" fill="none" stroke={color}>
        <rect x="10" y="18" width="180" height="64" rx="9" strokeWidth="5" />
        <rect x="17" y="25" width="166" height="50" rx="6" strokeWidth="2.2" />
        <text
          x="100"
          y="59"
          textAnchor="middle"
          fontWeight="bold"
          fontSize="28"
          letterSpacing="1.5"
          fill={color}
          stroke="none"
          fontStyle="italic"
        >
          {text}
        </text>
      </g>
    </svg>
  )
}
