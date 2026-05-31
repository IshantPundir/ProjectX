// components/dashboard/reports/theater/GlassBackdrop.tsx
'use client'

import { createContext, useContext, useEffect, useRef, type RefObject } from 'react'

/**
 * True glassmorphism over a <video>. Chromium does not reliably apply
 * `backdrop-filter` over a video element, so instead of blurring the backdrop we
 * render a SECOND, blurred copy of the same recording inside each glass panel and
 * `filter: blur()` it (which DOES work on video). The clone is sized + positioned
 * to line up 1:1 with the main full-bleed video, then clipped to the panel by the
 * panel's `overflow: hidden`, so each panel shows the correctly-aligned, blurred
 * slice of the video behind it — real frosted glass that tracks playback.
 *
 * Drop a <GlassBackdrop /> as the first child of any `.theater-glass` panel. It
 * reads the video src + main-video/root refs from <GlassProvider> (set up once in
 * ReviewTheater), so panels need no extra props.
 *
 * Perf: alignment is EVENT-DRIVEN (mount + ResizeObserver on the stage), NOT a
 * per-frame rAF — a per-frame getBoundingClientRect loop per panel thrashed
 * layout every frame. Time-sync is driven off the main video's play/pause/seek
 * events plus a low-frequency drift check.
 */

interface GlassCtx {
  src: string | null
  mainVideoRef: RefObject<HTMLVideoElement | null>
  rootRef: RefObject<HTMLElement | null>
}

const GlassContext = createContext<GlassCtx | null>(null)

export function GlassProvider({
  src,
  mainVideoRef,
  rootRef,
  children,
}: GlassCtx & { children: React.ReactNode }) {
  return (
    <GlassContext.Provider value={{ src, mainVideoRef, rootRef }}>
      {children}
    </GlassContext.Provider>
  )
}

export function GlassBackdrop() {
  const ctx = useContext(GlassContext)
  const hostRef = useRef<HTMLDivElement>(null)
  const cloneRef = useRef<HTMLVideoElement>(null)

  const src = ctx?.src ?? null
  const mainVideoRef = ctx?.mainVideoRef
  const rootRef = ctx?.rootRef

  // Position the blurred clone to overlap the stage 1:1. Event-driven only.
  useEffect(() => {
    if (!src || !rootRef) return
    const align = () => {
      const clone = cloneRef.current
      const host = hostRef.current
      const root = rootRef.current
      if (!clone || !host || !root) return
      const hostRect = host.getBoundingClientRect()
      const rootRect = root.getBoundingClientRect()
      if (!rootRect.width || !rootRect.height) return
      clone.style.width = `${rootRect.width}px`
      clone.style.height = `${rootRect.height}px`
      clone.style.left = `${rootRect.left - hostRect.left}px`
      clone.style.top = `${rootRect.top - hostRect.top}px`
    }
    align()
    const ro = new ResizeObserver(align)
    if (rootRef.current) ro.observe(rootRef.current)
    if (hostRef.current) ro.observe(hostRef.current)
    window.addEventListener('resize', align)
    // a couple of delayed re-aligns to catch the dialog's open layout settling
    const t1 = window.setTimeout(align, 60)
    const t2 = window.setTimeout(align, 250)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', align)
      window.clearTimeout(t1)
      window.clearTimeout(t2)
    }
  }, [src, rootRef])

  // Mirror play/pause/seek of the main video onto the muted clone, plus a slow
  // drift correction. No rAF.
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
    <div ref={hostRef} className="theater-glass-backdrop" aria-hidden="true">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- decorative blurred clone */}
      <video ref={cloneRef} src={src} muted playsInline preload="auto" />
    </div>
  )
}
