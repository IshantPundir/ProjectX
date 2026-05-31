// components/dashboard/reports/theater/GlassBackdrop.tsx
'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from 'react'

/**
 * True glassmorphism over a <video>, single-source.
 *
 * Chromium can't apply `backdrop-filter` over a <video>, so we blur the video
 * pixels directly with `filter: blur()` (which works). Rather than one blurred
 * clone PER panel (4 video decodes), we render ONE <GlassLayer> — a single
 * blurred, playback-synced copy of the recording covering the whole stage — and
 * clip it (via clip-path) to the union of the panels' rounded rects. Each panel
 * drops a <GlassBackdrop/> marker that registers its own element; the layer reads
 * those rects to build the clip. Result: real frosted glass over the live video,
 * one decode regardless of panel count, and per-panel rounding "for free".
 */

const PANEL_RADIUS = 16 // matches rounded-2xl on the panels

interface GlassCtx {
  src: string | null
  mainVideoRef: RefObject<HTMLVideoElement | null>
  rootRef: RefObject<HTMLElement | null>
  panels: RefObject<Set<HTMLElement>>
  registerPanel: (el: HTMLElement | null) => () => void
  version: number
}

const GlassContext = createContext<GlassCtx | null>(null)

export function GlassProvider({
  src,
  mainVideoRef,
  rootRef,
  children,
}: {
  src: string | null
  mainVideoRef: RefObject<HTMLVideoElement | null>
  rootRef: RefObject<HTMLElement | null>
  children: React.ReactNode
}) {
  const panels = useRef<Set<HTMLElement>>(new Set())
  const [version, setVersion] = useState(0)
  const registerPanel = useCallback((el: HTMLElement | null) => {
    if (!el) return () => {}
    panels.current.add(el)
    setVersion((v) => v + 1)
    return () => {
      panels.current.delete(el)
      setVersion((v) => v + 1)
    }
  }, [])
  return (
    <GlassContext.Provider
      value={{ src, mainVideoRef, rootRef, panels, registerPanel, version }}
    >
      {children}
    </GlassContext.Provider>
  )
}

/** Marker dropped as the first child of a `.theater-glass` panel. Registers the
 *  panel element with the layer; renders nothing visible. */
export function GlassBackdrop() {
  const ctx = useContext(GlassContext)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const panel = ref.current?.parentElement as HTMLElement | null
    if (!panel || !ctx) return
    return ctx.registerPanel(panel)
  }, [ctx])
  return <span ref={ref} aria-hidden="true" style={{ display: 'none' }} />
}

function roundedRectSubpath(
  x: number,
  y: number,
  w: number,
  h: number,
  radius: number,
): string {
  const r = Math.max(0, Math.min(radius, w / 2, h / 2))
  const x2 = x + w
  const y2 = y + h
  return (
    `M${x + r},${y} H${x2 - r} A${r},${r} 0 0 1 ${x2},${y + r} ` +
    `V${y2 - r} A${r},${r} 0 0 1 ${x2 - r},${y2} ` +
    `H${x + r} A${r},${r} 0 0 1 ${x},${y2 - r} ` +
    `V${y + r} A${r},${r} 0 0 1 ${x + r},${y} Z`
  )
}

/** The single blurred-video layer. Rendered once inside `.theater-root`, after
 *  the stage video/scrims and before the panels. */
export function GlassLayer() {
  const ctx = useContext(GlassContext)
  const layerRef = useRef<HTMLDivElement>(null)
  const cloneRef = useRef<HTMLVideoElement>(null)

  const src = ctx?.src ?? null
  const mainVideoRef = ctx?.mainVideoRef
  const rootRef = ctx?.rootRef
  const panels = ctx?.panels
  const version = ctx?.version ?? 0

  // Build the clip-path from the panels' rects (event-driven, no rAF).
  useEffect(() => {
    if (!src || !rootRef || !panels) return
    const layer = layerRef.current
    if (!layer) return

    const recompute = () => {
      const root = rootRef.current
      if (!root) return
      const rootRect = root.getBoundingClientRect()
      const subpaths: string[] = []
      panels.current.forEach((p) => {
        // skip hidden panels (e.g. auto-hidden controls) so no frosted rect lingers
        if (parseFloat(getComputedStyle(p).opacity || '1') < 0.05) return
        const r = p.getBoundingClientRect()
        if (!r.width || !r.height) return
        subpaths.push(
          roundedRectSubpath(
            r.left - rootRect.left,
            r.top - rootRect.top,
            r.width,
            r.height,
            PANEL_RADIUS,
          ),
        )
      })
      // empty clip → nothing painted (no full-screen blur flash)
      layer.style.clipPath = `path('${subpaths.join(' ')}')`
    }

    recompute()
    const ro = new ResizeObserver(recompute)
    if (rootRef.current) ro.observe(rootRef.current)
    panels.current.forEach((p) => ro.observe(p))
    // attribute changes (controls data-visible / style / class) → reclip, plus a
    // trailing pass so the opacity transition has settled before we drop a rect
    const mo = new MutationObserver(() => {
      recompute()
      window.setTimeout(recompute, 320)
    })
    if (rootRef.current) {
      mo.observe(rootRef.current, {
        attributes: true,
        subtree: true,
        attributeFilter: ['data-visible', 'style', 'class'],
      })
    }
    window.addEventListener('resize', recompute)
    const t1 = window.setTimeout(recompute, 60)
    const t2 = window.setTimeout(recompute, 260)
    return () => {
      ro.disconnect()
      mo.disconnect()
      window.removeEventListener('resize', recompute)
      window.clearTimeout(t1)
      window.clearTimeout(t2)
    }
  }, [src, rootRef, panels, version])

  // Mirror play/pause/seek of the main video onto the muted blurred clone.
  useEffect(() => {
    if (!src || !mainVideoRef) return
    const clone = cloneRef.current
    if (!clone) return
    clone.muted = true
    const main = mainVideoRef.current
    const syncTime = () => {
      if (main && Math.abs(clone.currentTime - main.currentTime) > 0.3) {
        clone.currentTime = main.currentTime
      }
    }
    const onPlay = () => {
      syncTime()
      void clone.play?.().catch(() => {})
    }
    const onPause = () => clone.pause?.()
    const onSeek = () => {
      if (main) clone.currentTime = main.currentTime
    }
    if (main) {
      main.addEventListener('play', onPlay)
      main.addEventListener('pause', onPause)
      main.addEventListener('seeking', onSeek)
      if (!main.paused) onPlay()
    }
    const drift = window.setInterval(() => {
      if (main && !main.paused) syncTime()
    }, 2000)
    return () => {
      if (main) {
        main.removeEventListener('play', onPlay)
        main.removeEventListener('pause', onPause)
        main.removeEventListener('seeking', onSeek)
      }
      window.clearInterval(drift)
    }
  }, [src, mainVideoRef])

  if (!src) return null
  return (
    <div ref={layerRef} className="theater-glass-layer" aria-hidden="true">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- decorative blurred clone */}
      <video ref={cloneRef} src={src} muted playsInline preload="auto" />
    </div>
  )
}
