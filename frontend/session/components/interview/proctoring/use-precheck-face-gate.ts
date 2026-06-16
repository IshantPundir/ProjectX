'use client'

import { useEffect, useState, type RefObject } from 'react'

import { createFaceDetector } from './vision/face-detector'
import { extractFaceBoxes, type PixelBox } from './vision/face-box'

const DETECT_INTERVAL_MS = 250 // ~4fps presence sampling (presence is slow-moving)
const MODEL_TIMEOUT_MS = 8000 // after this, degrade to non-blocking (no lockout)

export interface FaceGateState {
  /** Vision model loaded + running. */
  ready: boolean
  /** Model failed or was too slow to load — the caller must NOT hard-block. */
  failed: boolean
  /** Faces detected on the most recent tick. */
  faceCount: number
  /** Detected boxes in the video's intrinsic pixel space (for the on-screen marker). */
  boxes: PixelBox[]
  /** The video's intrinsic frame size when the last detection ran. */
  frame: { width: number; height: number } | null
}

const EMPTY: FaceGateState = { ready: false, failed: false, faceCount: 0, boxes: [], frame: null }

type Detector = {
  detectForVideo: (v: HTMLVideoElement, t: number) => unknown
  close?: () => void
}

/**
 * Runs the MediaPipe FaceDetector on the live <video> preview during the
 * pre-check camera step (there is no LiveKit session yet, so this can't reuse
 * use-vision-guard). Exposes the face count + boxes so the camera step can mark
 * the candidate's face and gate the Start button on exactly one face. Detector +
 * WASM are same-origin (`public/mediapipe/*`). Degrades to `failed` (caller does
 * not block) if the model can't load — a candidate is never trapped.
 */
export function usePreCheckFaceGate(
  videoRef: RefObject<HTMLVideoElement | null>,
  enabled: boolean,
): FaceGateState {
  const [state, setState] = useState<FaceGateState>(EMPTY)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let raf = 0
    let detector: Detector | null = null
    let lastDetectAt = 0

    const timeout = window.setTimeout(() => {
      if (!cancelled) setState((s) => (s.ready ? s : { ...s, failed: true }))
    }, MODEL_TIMEOUT_MS)

    const tick = () => {
      if (cancelled || !detector) return
      const video = videoRef.current
      if (!video || video.readyState < 2 || video.videoWidth === 0) {
        raf = requestAnimationFrame(tick)
        return
      }
      const now = performance.now()
      if (now - lastDetectAt >= DETECT_INTERVAL_MS) {
        lastDetectAt = now
        const result = detector.detectForVideo(video, now) as Parameters<typeof extractFaceBoxes>[0]
        const { count, boxes } = extractFaceBoxes(result)
        setState({
          ready: true,
          failed: false,
          faceCount: count,
          boxes,
          frame: { width: video.videoWidth, height: video.videoHeight },
        })
      }
      raf = requestAnimationFrame(tick)
    }

    createFaceDetector()
      .then((det) => {
        if (cancelled) {
          ;(det as Detector).close?.()
          return
        }
        detector = det as Detector
        window.clearTimeout(timeout)
        setState((s) => ({ ...s, ready: true, failed: false }))
        raf = requestAnimationFrame(tick)
      })
      .catch(() => {
        if (!cancelled) setState((s) => ({ ...s, failed: true }))
      })

    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      cancelAnimationFrame(raf)
      detector?.close?.()
      setState(EMPTY)
    }
  }, [enabled, videoRef])

  return state
}
