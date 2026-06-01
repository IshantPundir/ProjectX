// components/dashboard/reports/theater/useVideoController.ts
'use client'

import {
  useCallback,
  useEffect,
  useState,
  type MutableRefObject,
  type RefObject,
} from 'react'

import type { PlaybackSeekApi } from '../SessionPlayback'

const RATES = [1, 1.5, 2] as const

export function clockFromSec(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0
  const total = Math.floor(sec)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export interface VideoController {
  playing: boolean
  currentSec: number
  durationSec: number
  bufferedSec: number
  volume: number
  muted: boolean
  rate: number
  isFullscreen: boolean
  togglePlay: () => void
  seekToSec: (sec: number) => void
  setVolume: (v: number) => void
  toggleMute: () => void
  cycleRate: () => void
}

/** Owns transport state for the theater's custom controls. Attaches listeners to
 * the <video> once it exists (gated by `enabled`), exposes the ms-based seek API
 * the rest of the theater uses (questions/flags) via `seekApiRef`, and reports
 * the engine-relative playhead through `onCurrentMs`.
 *
 * @param onCurrentMs Must be render-stable (a useState setter or useCallback-wrapped fn);
 * an inline arrow would re-attach all media listeners on every parent render. */
export function useVideoController(
  videoRef: RefObject<HTMLVideoElement | null>,
  enabled: boolean,
  offsetMs: number,
  seekApiRef: MutableRefObject<PlaybackSeekApi | null>,
  onCurrentMs: (ms: number) => void,
): VideoController {
  const [playing, setPlaying] = useState(false)
  const [currentSec, setCurrentSec] = useState(0)
  const [durationSec, setDurationSec] = useState(0)
  const [bufferedSec, setBufferedSec] = useState(0)
  const [volume, setVolumeState] = useState(1)
  const [muted, setMuted] = useState(false)
  const [rate, setRate] = useState(1)
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    const v = videoRef.current
    if (!v || !enabled) return
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onTime = () => {
      setCurrentSec(v.currentTime)
      onCurrentMs(v.currentTime * 1000 - offsetMs)
    }
    const onDur = () => setDurationSec(Number.isFinite(v.duration) ? v.duration : 0)
    const onProgress = () => {
      try {
        if (v.buffered.length) setBufferedSec(v.buffered.end(v.buffered.length - 1))
      } catch {
        /* buffered can throw before metadata; ignore */
      }
    }
    const onVol = () => {
      setVolumeState(v.volume)
      setMuted(v.muted)
    }
    const onRate = () => setRate(v.playbackRate)
    v.addEventListener('play', onPlay)
    v.addEventListener('pause', onPause)
    v.addEventListener('timeupdate', onTime)
    v.addEventListener('durationchange', onDur)
    v.addEventListener('progress', onProgress)
    v.addEventListener('volumechange', onVol)
    v.addEventListener('ratechange', onRate)
    // sync initial state
    onDur()
    onVol()
    return () => {
      v.removeEventListener('play', onPlay)
      v.removeEventListener('pause', onPause)
      v.removeEventListener('timeupdate', onTime)
      v.removeEventListener('durationchange', onDur)
      v.removeEventListener('progress', onProgress)
      v.removeEventListener('volumechange', onVol)
      v.removeEventListener('ratechange', onRate)
    }
  }, [videoRef, enabled, offsetMs, onCurrentMs])

  useEffect(() => {
    const onFs = () => setIsFullscreen(document.fullscreenElement != null)
    document.addEventListener('fullscreenchange', onFs)
    onFs()
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  // ms-based seek used by question/flag jumps (kept identical to old TheaterStage)
  useEffect(() => {
    seekApiRef.current = {
      seekToMs: (ms: number) => {
        const v = videoRef.current
        if (!v) return
        v.currentTime = Math.max(0, (ms + offsetMs) / 1000)
        // Catch the benign "play() interrupted by pause()" rejection that fires
        // when a rapid re-seek/pause interrupts this play promise.
        void v.play?.()?.catch(() => {})
      },
    }
    return () => {
      seekApiRef.current = null
    }
  }, [videoRef, seekApiRef, offsetMs])

  const togglePlay = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) void v.play?.()?.catch(() => {})
    else v.pause?.()
  }, [videoRef])

  const seekToSec = useCallback(
    (sec: number) => {
      const v = videoRef.current
      if (!v) return
      v.currentTime = Math.max(0, sec)
    },
    [videoRef],
  )

  const setVolume = useCallback(
    (val: number) => {
      const v = videoRef.current
      if (!v) return
      v.muted = false
      v.volume = Math.min(1, Math.max(0, val))
    },
    [videoRef],
  )

  const toggleMute = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    v.muted = !v.muted
  }, [videoRef])

  const cycleRate = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    const idx = (RATES as readonly number[]).indexOf(v.playbackRate)
    v.playbackRate = RATES[(idx + 1) % RATES.length] ?? 1
  }, [videoRef])

  return {
    playing,
    currentSec,
    durationSec,
    bufferedSec,
    volume,
    muted,
    rate,
    isFullscreen,
    togglePlay,
    seekToSec,
    setVolume,
    toggleMute,
    cycleRate,
  }
}
