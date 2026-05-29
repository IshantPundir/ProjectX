'use client'

import { useEffect, useRef, useState } from 'react'
import { useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'

import { createFaceLandmarker, blendshape } from './vision/face-landmarker'
import { matrixToHeadPose } from './vision/head-pose'
import { classifyGazeZone, blinkScore, isBlinking, signalQuality, poseToGazePoint } from './vision/gaze'
import type { VisionSignals } from './vision/types'
import { NUDGE_SUSTAIN_MS, type VisionNudgeKind } from './nudge-kinds'

const GAZE_TRAIL_MAX = 24 // recent gaze points kept for the dev fading trail

const EMPTY: VisionSignals = {
  faceCount: 0, pose: null, gazeZone: null, gazePoint: null, gazeTrail: [],
  blinking: false, earValue: null, quality: 'unscorable', fps: 0,
}

export interface UseVisionGuardArgs {
  armed: boolean
  /** Fired once per sustained occurrence (rising edge), re-armed when the
   * condition clears. Wired to the proctoring controller's report() so vision
   * violations count toward the shared soft-violation limit. */
  onViolation: (kind: VisionNudgeKind) => void
}

export interface VisionGuardState {
  signals: VisionSignals
}

export function useVisionGuard({ armed, onViolation }: UseVisionGuardArgs): VisionGuardState {
  const { localParticipant } = useLocalParticipant()
  // Stable refs so the detection-loop effect doesn't restart when the LiveKit
  // participant identity or the onViolation callback identity changes.
  const participantRef = useRef(localParticipant)
  const onViolationRef = useRef(onViolation)
  useEffect(() => {
    participantRef.current = localParticipant
    onViolationRef.current = onViolation
  })

  const [signals, setSignals] = useState<VisionSignals>(EMPTY)

  useEffect(() => {
    if (!armed) return
    let cancelled = false
    let raf = 0
    let landmarker: { detectForVideo: (v: HTMLVideoElement, t: number) => unknown; close: () => void } | null = null
    let last = performance.now()
    // Debounce state lives for one armed session (resets on re-arm).
    let trail: { x: number; y: number }[] = []
    const since: Partial<Record<VisionNudgeKind, number>> = {}
    const fired = new Set<VisionNudgeKind>()

    // One violation per sustained occurrence: fire on the rising edge once the
    // condition has held past its window, then re-arm only after it clears.
    const maybeFire = (kind: VisionNudgeKind, active: boolean, now: number) => {
      if (!active) {
        delete since[kind]
        fired.delete(kind)
        return
      }
      since[kind] ??= now
      if (!fired.has(kind) && now - since[kind]! >= NUDGE_SUSTAIN_MS[kind]) {
        fired.add(kind)
        onViolationRef.current(kind)
      }
    }

    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true

    const pub = participantRef.current.getTrackPublication(Track.Source.Camera)
    const track = pub?.track
    if (track) track.attach(video)

    const tick = () => {
      if (cancelled || !landmarker) return
      if (video.readyState < 2) { raf = requestAnimationFrame(tick); return } // wait for a decoded frame
      const now = performance.now()
      const fps = 1000 / Math.max(1, now - last)
      last = now

      const res = landmarker.detectForVideo(video, now) as {
        faceBlendshapes?: Array<{ categories: Array<{ categoryName: string; score: number }> }>
        facialTransformationMatrixes?: Array<{ data: number[] }>
      }
      const faceCount = res.facialTransformationMatrixes?.length ?? 0
      const cats = res.faceBlendshapes?.[0]?.categories
      const mtx = res.facialTransformationMatrixes?.[0]?.data
      // HEAD-POSE-ONLY gaze: the live plane is a coarse DETERRENT (the accurate,
      // eye-aware gaze is the server-side report model). No iris — it added
      // fragility for accuracy we don't need live.
      const pose = faceCount > 0 && mtx ? matrixToHeadPose(mtx) : null
      const ear = cats ? blinkScore(blendshape(cats, 'eyeBlinkLeft'), blendshape(cats, 'eyeBlinkRight')) : null
      const quality = signalQuality({
        faceConfidence: faceCount > 0 ? 1 : 0,
        brightness: 0.5, // Plan A: brightness/glare proxies refined in Plan B
        eyeGlare: 0,
      })
      const zone = pose ? classifyGazeZone(pose) : null
      const gazePoint = pose ? poseToGazePoint(pose) : null
      if (gazePoint) trail = [...trail, gazePoint].slice(-GAZE_TRAIL_MAX)

      setSignals({
        faceCount, pose, gazeZone: zone, gazePoint, gazeTrail: trail,
        blinking: ear !== null && isBlinking(ear), earValue: ear, quality, fps,
      })

      // Rising-edge violation events → the proctoring controller (soft, counted).
      maybeFire('multiple_faces', faceCount >= 2, now)
      maybeFire('face_not_visible', faceCount === 0, now)
      maybeFire('looking_away_sustained', zone !== null && zone !== 'center', now)

      raf = requestAnimationFrame(tick)
    }

    createFaceLandmarker().then((lm) => {
      if (cancelled) { lm.close?.(); return }
      landmarker = lm as typeof landmarker
      void video.play()?.catch(() => {})
      raf = requestAnimationFrame(tick)
    })

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      if (track) track.detach(video)
      landmarker?.close()
      setSignals(EMPTY)
    }
  }, [armed])

  return { signals }
}
