'use client'

import { useEffect, useRef } from 'react'

import { Aura } from '@/components/agents-ui/aura'

interface IntroLoaderProps {
  /**
   * Optional callback for forensic logging. Called once when the loader
   * mounts. Use to record `intro_loader.shown_at` for perceived-latency
   * measurement.
   */
  onShown?: () => void
}

/**
 * Pre-speech loading state shown after `/start` succeeds but before the
 * agent's first audio frame plays. Hides the LiveKit boot + first
 * Speaker LLM + TTS latency (~2-4s) behind an in-character "preparing
 * your interviewer" affordance.
 *
 * See docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §3.
 */
export function IntroLoader({ onShown }: IntroLoaderProps) {
  useOnce(() => {
    onShown?.()
  })

  return (
    <div
      role="status"
      aria-live="polite"
      className="px-cine-bg flex min-h-screen w-full flex-col items-center justify-center gap-8"
    >
      <Aura state="connecting" audioTrack={undefined} size="xl" />
      <div className="flex flex-col items-center gap-1.5 text-center">
        <p className="font-serif text-xl text-px-fg">Preparing your interviewer…</p>
        <p className="text-sm text-px-fg-3">This usually takes just a few seconds.</p>
      </div>
    </div>
  )
}

/**
 * useOnce: fires a callback exactly once for the lifetime of the
 * component instance. Uses a ref-guarded effect so that React 19's
 * dev-mode double-invoke does not double-fire the callback.
 */
function useOnce(callback: () => void) {
  const firedRef = useRef(false)
  const callbackRef = useRef(callback)
  callbackRef.current = callback

  useEffect(() => {
    if (firedRef.current) return
    firedRef.current = true
    callbackRef.current()
  }, [])
}
