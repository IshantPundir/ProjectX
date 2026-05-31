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

  // Mirror play/pause/seek of the main video onto the muted clone.
  useEffect(() => {
    if (!src || !mainVideoRef) return
    const clone = cloneRef.current
    if (!clone) return
    const main = mainVideoRef.current
    clone.muted = true
    const onPlay = () => void clone.play?.().catch(() => {})
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
    return () => {
      if (main) {
        main.removeEventListener('play', onPlay)
        main.removeEventListener('pause', onPause)
        main.removeEventListener('seeking', onSeek)
      }
    }
  }, [src, mainVideoRef])

  // Keep the clone aligned to the main video's rect (handles layout/resize) and
  // correct any playback drift. One rAF loop, cheap reads only.
  useEffect(() => {
    if (!src || !mainVideoRef || !rootRef) return
    let raf = 0
    let frame = 0
    const tick = () => {
      const clone = cloneRef.current
      const host = hostRef.current
      const root = rootRef.current
      const main = mainVideoRef.current
      if (clone && host && root) {
        const hostRect = host.getBoundingClientRect()
        const rootRect = root.getBoundingClientRect()
        if (rootRect.width && rootRect.height) {
          clone.style.width = `${rootRect.width}px`
          clone.style.height = `${rootRect.height}px`
          clone.style.left = `${rootRect.left - hostRect.left}px`
          clone.style.top = `${rootRect.top - hostRect.top}px`
        }
        // correct drift ~4×/s, not every frame (seeking a video is expensive)
        if (main && frame % 15 === 0 && Math.abs(clone.currentTime - main.currentTime) > 0.4) {
          clone.currentTime = main.currentTime
        }
      }
      frame += 1
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [src, mainVideoRef, rootRef])

  if (!src) return null
  return (
    <div ref={hostRef} className="theater-glass-backdrop" aria-hidden="true">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- decorative blurred clone */}
      <video ref={cloneRef} src={src} muted playsInline preload="auto" />
    </div>
  )
}
