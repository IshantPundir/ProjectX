'use client'

import { useEffect, useRef, useState } from 'react'
import { useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'

import { createFaceLandmarker, blendshape } from './vision/face-landmarker'
import { createFaceDetector, summarizeDetections, type FaceCountSummary } from './vision/face-detector'
import { matrixToHeadPose } from './vision/head-pose'
import { classifyGazeZone, blinkScore, isBlinking, signalQuality, poseToGazePoint } from './vision/gaze'
import type { VisionSignals } from './vision/types'
import { NUDGE_SUSTAIN_MS, type VisionNudgeKind } from './nudge-kinds'

const GAZE_TRAIL_MAX = 24 // recent gaze points kept for the dev fading trail
const DETECT_INTERVAL_MS = 350 // ~3fps face-count sampling (presence is slow-moving)

const EMPTY: VisionSignals = {
  faceCount: 0, pose: null, gazeZone: null, gazePoint: null, gazeTrail: [],
  blinking: false, earValue: null, quality: 'unscorable', fps: 0,
}

type Model = { detectForVideo: (v: HTMLVideoElement, t: number) => unknown; close: () => void } | null

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
  const participantRef = useRef(localParticipant)
  const onViolationRef = useRef(onViolation)
  // Stable refs so the detection-loop effect doesn't restart when the LiveKit
  // participant identity or the onViolation callback identity changes.
  useEffect(() => {
    participantRef.current = localParticipant
    onViolationRef.current = onViolation
  })

  const [signals, setSignals] = useState<VisionSignals>(EMPTY)

  useEffect(() => {
    if (!armed) return
    let cancelled = false
    let raf = 0
    let landmarker: Model = null
    let detector: Model = null
    let last = performance.now()
    let trail: { x: number; y: number }[] = []
    // Authoritative face COUNT comes from the throttled detector.
    let lastDetectAt = 0 // 0 ⇒ the detector runs on the first frame
    let faceSummary: FaceCountSummary = { faceCount: 0, topConfidence: 0 }
    const since: Partial<Record<VisionNudgeKind, number>> = {}
    const fired = new Set<VisionNudgeKind>()

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
      if (cancelled || !landmarker || !detector) return
      if (video.readyState < 2) { raf = requestAnimationFrame(tick); return } // wait for a decoded frame
      const now = performance.now()
      const fps = 1000 / Math.max(1, now - last)
      last = now

      // Landmarker every frame: head pose + blink of the PRIMARY face only.
      const lm = landmarker.detectForVideo(video, now) as {
        faceBlendshapes?: Array<{ categories: Array<{ categoryName: string; score: number }> }>
        facialTransformationMatrixes?: Array<{ data: number[] }>
      }
      const cats = lm.faceBlendshapes?.[0]?.categories
      const mtx = lm.facialTransformationMatrixes?.[0]?.data

      // Detector throttled: authoritative multi/zero-face COUNT.
      if (detector && now - lastDetectAt >= DETECT_INTERVAL_MS) {
        lastDetectAt = now
        faceSummary = summarizeDetections(
          detector.detectForVideo(video, now) as { detections?: Array<{ categories?: Array<{ score: number }> }> },
        )
      }
      const faceCount = faceSummary.faceCount

      // HEAD-POSE-ONLY gaze: live plane is a coarse DETERRENT (accurate gaze is
      // the server model). Pose derives from the landmarker matrix, independent
      // of the detector count.
      const pose = mtx ? matrixToHeadPose(mtx) : null
      const ear = cats ? blinkScore(blendshape(cats, 'eyeBlinkLeft'), blendshape(cats, 'eyeBlinkRight')) : null
      const quality = signalQuality({
        faceConfidence: faceSummary.topConfidence,
        brightness: 0.5, // brightness/glare proxies refined in a later plan
        eyeGlare: 0,
      })
      const zone = pose ? classifyGazeZone(pose) : null
      const gazePoint = pose ? poseToGazePoint(pose) : null
      if (gazePoint) trail = [...trail, gazePoint].slice(-GAZE_TRAIL_MAX)

      setSignals({
        faceCount, pose, gazeZone: zone, gazePoint, gazeTrail: trail,
        blinking: ear !== null && isBlinking(ear), earValue: ear, quality, fps,
      })

      maybeFire('multiple_faces', faceCount >= 2, now)
      maybeFire('face_not_visible', faceCount === 0, now)
      maybeFire('looking_away_sustained', zone !== null && zone !== 'center', now)

      raf = requestAnimationFrame(tick)
    }

    Promise.all([createFaceLandmarker(), createFaceDetector()]).then(([lm, det]) => {
      if (cancelled) { lm.close?.(); det.close?.(); return }
      landmarker = lm as Model
      detector = det as Model
      void video.play()?.catch(() => {})
      raf = requestAnimationFrame(tick)
    })

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      if (track) track.detach(video)
      landmarker?.close()
      detector?.close()
      setSignals(EMPTY)
    }
  }, [armed])

  return { signals }
}
