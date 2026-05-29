'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'

import { createFaceLandmarker, blendshape } from './vision/face-landmarker'
import { matrixToHeadPose } from './vision/head-pose'
import { classifyGazeZone, blinkScore, isBlinking, signalQuality, poseToGazePoint } from './vision/gaze'
import { ReadingAccumulator } from './vision/reading'
import type { VisionSignals } from './vision/types'
import { NUDGE_SUSTAIN_MS, type VisionNudgeKind } from './nudge-kinds'

const GAZE_TRAIL_MAX = 24 // recent gaze points kept for the dev fading trail

const EMPTY: VisionSignals = {
  faceCount: 0, pose: null, gazeZone: null, gazePoint: null, gazeTrail: [],
  blinking: false, earValue: null, quality: 'unscorable', fps: 0,
}

export interface UseVisionGuardArgs {
  armed: boolean
  onNudge: (kind: VisionNudgeKind) => void
}

export interface VisionGuardState {
  signals: VisionSignals
}

export function useVisionGuard({ armed, onNudge }: UseVisionGuardArgs): VisionGuardState {
  const { localParticipant } = useLocalParticipant()
  // Stable ref so the detection-loop effect doesn't restart when the LiveKit
  // participant object identity changes (e.g. test re-renders, SDK reconnect).
  const participantRef = useRef(localParticipant)
  // Keep the ref current without re-running the detection effect when the
  // participant object identity changes (test re-renders / SDK reconnect).
  useEffect(() => {
    participantRef.current = localParticipant
  })

  const [signals, setSignals] = useState<VisionSignals>(EMPTY)
  const reading = useRef(new ReadingAccumulator())
  const since = useRef<Partial<Record<VisionNudgeKind, number>>>({})
  const gazeTrail = useRef<{ x: number; y: number }[]>([])

  const maybeNudge = useCallback(
    (kind: VisionNudgeKind, active: boolean, now: number) => {
      if (!active) { delete since.current[kind]; return }
      const start = since.current[kind] ?? now
      since.current[kind] = start
      if (now - start >= NUDGE_SUSTAIN_MS[kind]) {
        onNudge(kind)
        // Push the next-fire window far forward to avoid repeated firings
        // until the condition clears and restarts.
        since.current[kind] = now + 1e9
      }
    },
    [onNudge],
  )

  useEffect(() => {
    if (!armed) return
    let cancelled = false
    let raf = 0
    let landmarker: { detectForVideo: (v: HTMLVideoElement, t: number) => unknown; close: () => void } | null = null
    let last = performance.now()
    const accumulator = reading.current

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
      const pose = faceCount > 0 && mtx ? matrixToHeadPose(mtx) : null
      const iris = {
        in: Math.max(blendshape(cats, 'eyeLookInLeft'), blendshape(cats, 'eyeLookInRight')),
        out: Math.max(blendshape(cats, 'eyeLookOutLeft'), blendshape(cats, 'eyeLookOutRight')),
        up: Math.max(blendshape(cats, 'eyeLookUpLeft'), blendshape(cats, 'eyeLookUpRight')),
        down: Math.max(blendshape(cats, 'eyeLookDownLeft'), blendshape(cats, 'eyeLookDownRight')),
      }
      const ear = cats ? blinkScore(blendshape(cats, 'eyeBlinkLeft'), blendshape(cats, 'eyeBlinkRight')) : null
      const quality = signalQuality({
        faceConfidence: faceCount > 0 ? 1 : 0,
        brightness: 0.5, // Plan A: brightness/glare proxies refined in Plan B
        eyeGlare: 0,
      })
      const zone = pose ? classifyGazeZone(pose, iris) : null
      const gazePoint = pose ? poseToGazePoint(pose) : null
      if (zone) accumulator.push(zone, now)
      if (gazePoint) gazeTrail.current = [...gazeTrail.current, gazePoint].slice(-GAZE_TRAIL_MAX)

      setSignals({
        faceCount, pose, gazeZone: zone, gazePoint, gazeTrail: gazeTrail.current,
        blinking: ear !== null && isBlinking(ear), earValue: ear, quality, fps,
      })

      maybeNudge('face_not_visible', faceCount === 0, now)
      maybeNudge('multiple_faces', faceCount >= 2, now)
      maybeNudge('looking_away_sustained', accumulator.isReading(), now)

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
      accumulator.reset()
      since.current = {}
      gazeTrail.current = []
    }
  // participantRef is intentionally omitted: it's a stable ref, not a dep.
  }, [armed, maybeNudge])

  return { signals }
}
