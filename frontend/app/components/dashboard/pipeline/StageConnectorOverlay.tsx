'use client'

import { useEffect, useRef, useState } from 'react'

type Paths = {
  d: string
  start: { x: number; y: number }
  end: { x: number; y: number }
}

type Props = {
  selectedStageId: string | null
  hidden?: boolean
}

export function StageConnectorOverlay({ selectedStageId, hidden }: Props) {
  const [paths, setPaths] = useState<Paths | null>(null)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    function recompute() {
      if (!selectedStageId) {
        setPaths(null)
        return
      }
      const card = document.querySelector<HTMLElement>(
        `[data-stage-card-id="${selectedStageId}"]`,
      )
      const panel = document.querySelector<HTMLElement>(
        `[data-inspector-anchor="true"]`,
      )
      const container = document.querySelector<HTMLElement>(
        `[data-pipeline-container="true"]`,
      )
      if (!card || !panel || !container) {
        setPaths(null)
        return
      }

      const cardRect = card.getBoundingClientRect()
      const panelRect = panel.getBoundingClientRect()
      const containerRect = container.getBoundingClientRect()

      const startX = cardRect.right - containerRect.left
      const startY = cardRect.top + cardRect.height / 2 - containerRect.top
      const endX = panelRect.left - containerRect.left
      const endY = panelRect.top + 20 - containerRect.top

      const dx = endX - startX
      const ctrl1X = startX + Math.max(dx * 0.5, 40)
      const ctrl2X = endX - Math.max(dx * 0.5, 40)
      const d = `M ${startX},${startY} C ${ctrl1X},${startY} ${ctrl2X},${endY} ${endX},${endY}`

      setPaths({
        d,
        start: { x: startX, y: startY },
        end: { x: endX, y: endY },
      })
    }

    function scheduleRecompute() {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        recompute()
      })
    }

    // Initial measurement — always deferred through rAF so setState never
    // fires synchronously inside the effect body (avoids cascading renders).
    scheduleRecompute()

    if (!selectedStageId) {
      return () => {
        if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      }
    }

    const container = document.querySelector<HTMLElement>(
      `[data-pipeline-container="true"]`,
    )
    const observer = new ResizeObserver(scheduleRecompute)
    if (container) observer.observe(container)

    window.addEventListener('resize', scheduleRecompute)

    // Recompute on scroll inside the left column (flow column scrolls internally)
    const flowColumn = document.querySelector<HTMLElement>(
      `[data-pipeline-container="true"] > div:first-child`,
    )
    flowColumn?.addEventListener('scroll', scheduleRecompute)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', scheduleRecompute)
      flowColumn?.removeEventListener('scroll', scheduleRecompute)
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [selectedStageId])

  if (hidden || !paths) return null

  return (
    <svg
      className="pointer-events-none absolute inset-0 z-20 motion-reduce:hidden"
      aria-hidden="true"
      style={{ width: '100%', height: '100%' }}
    >
      <defs>
        <linearGradient
          id="stageConnectorGradient"
          x1="0%"
          y1="0%"
          x2="100%"
          y2="0%"
        >
          <stop offset="0%" style={{ stopColor: 'var(--px-accent-soft)' }} stopOpacity="0.8" />
          <stop offset="100%" style={{ stopColor: 'var(--px-accent)' }} stopOpacity="0.9" />
        </linearGradient>
      </defs>
      <path
        d={paths.d}
        fill="none"
        stroke="url(#stageConnectorGradient)"
        strokeWidth="2"
        strokeDasharray="4 4"
        strokeLinecap="round"
        className="transition-all duration-300 ease-out"
        style={{ filter: 'drop-shadow(0 0 4px var(--px-accent-line))' }}
      />
      <circle cx={paths.start.x} cy={paths.start.y} r="4" style={{ fill: 'var(--px-accent)' }} />
      <circle cx={paths.end.x} cy={paths.end.y} r="4" style={{ fill: 'var(--px-accent)' }} />
    </svg>
  )
}
