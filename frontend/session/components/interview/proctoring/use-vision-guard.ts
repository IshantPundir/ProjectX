'use client'

import { useEffect, useRef, useState } from 'react'
import { useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'

import { createFaceLandmarker, blendshape } from './vision/face-landmarker'
import { createFaceDetector, summarizeDetections, type FaceCountSummary } from './vision/face-detector'
import { matrixToHeadPose } from './vision/head-pose'
import { classifyGazeZone, blinkScore, isBlinking, signalQuality, poseToGazePoint } from './vision/gaze'
import { ReadingAccumulator } from './vision/reading'
import type { VisionSignals } from './vision/types'
import { NUDGE_SUSTAIN_MS, type VisionNudgeKind } from './nudge-kinds'
import { env } from '@/lib/env'

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
    const reader = new ReadingAccumulator()

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
    // MUST be connected to the document. Chromium does not guarantee frame
    // DECODE for a <video> that is detached from the DOM — readyState can stall
    // below HAVE_CURRENT_DATA (2), so the tick() guard below early-returns
    // forever and detectForVideo never runs. This is exactly why the pre-check
    // gate (a mounted, visible <video>) works on every device while this guard
    // silently produced nothing on some laptops. Mount it off-screen — but NOT
    // via display:none / visibility:hidden, which ALSO suspend decoding.
    video.style.cssText =
      'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none;'
    video.setAttribute('aria-hidden', 'true')
    document.body.appendChild(video)

    const debug = env.NEXT_PUBLIC_PROCTORING_DEBUG
    if (debug) console.info('[proctoring] vision guard armed')

    // Attach the LOCAL camera track REACTIVELY rather than once at arm time. The
    // publication often does not exist yet when the guard arms: arming is gated
    // on the agent starting to speak, and on slower devices the camera can
    // finish publishing AFTER that. Grabbing the track once would miss it and
    // leave the video with no source (readyState stuck at 0, fps 0, no
    // detection — confirmed on a slower laptop). Re-check every tick until the
    // track appears, then attach + play exactly once.
    type AttachableTrack = { attach: (el: HTMLVideoElement) => void; detach: (el: HTMLVideoElement) => void }
    let attachedTrack: AttachableTrack | null = null
    const ensureCameraAttached = (): boolean => {
      if (attachedTrack) return true
      const t = participantRef.current.getTrackPublication(Track.Source.Camera)?.track as
        | AttachableTrack
        | undefined
      if (!t) return false
      t.attach(video)
      attachedTrack = t
      void video.play()?.catch(() => {})
      if (debug) console.info('[proctoring] camera track attached')
      return true
    }

    let sawFirstFrame = false
    let lastDebugAt = 0

    const tick = () => {
      if (cancelled || !landmarker || !detector) return
      // Wait for the camera track to publish AND a frame to decode.
      if (!ensureCameraAttached() || video.readyState < 2) { raf = requestAnimationFrame(tick); return }
      const now = performance.now()
      const fps = 1000 / Math.max(1, now - last)
      last = now

      if (debug && !sawFirstFrame) {
        sawFirstFrame = true
        console.info('[proctoring] first decoded frame', {
          readyState: video.readyState, w: video.videoWidth, h: video.videoHeight,
        })
      }

      // Each model runs inside its own try/catch and the loop ALWAYS
      // re-schedules below. A per-frame inference failure on one model (or one
      // bad frame) must never silently kill the whole detection loop — that is
      // exactly how a GPU-delegate stall on a software-WebGL device used to take
      // out face-counting too, since the landmarker call sat ahead of it.
      // Landmarker every frame: head pose + blink of the PRIMARY face only.
      let cats: Array<{ categoryName: string; score: number }> | undefined
      let mtx: number[] | undefined
      try {
        const lm = landmarker.detectForVideo(video, now) as {
          faceBlendshapes?: Array<{ categories: Array<{ categoryName: string; score: number }> }>
          facialTransformationMatrixes?: Array<{ data: number[] }>
        }
        cats = lm.faceBlendshapes?.[0]?.categories
        mtx = lm.facialTransformationMatrixes?.[0]?.data
      } catch {
        // Head-pose plane unavailable on this device — degrade to face-count
        // only (pose/gaze become null) rather than aborting the loop.
        cats = undefined
        mtx = undefined
      }

      // Detector throttled: authoritative multi/zero-face COUNT.
      if (detector && now - lastDetectAt >= DETECT_INTERVAL_MS) {
        lastDetectAt = now
        try {
          faceSummary = summarizeDetections(
            detector.detectForVideo(video, now) as { detections?: Array<{ categories?: Array<{ score: number }> }> },
          )
        } catch {
          // Keep the last known count rather than killing the loop.
        }
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

      if (debug && now - lastDebugAt >= 1000) {
        lastDebugAt = now
        console.info('[proctoring] vision tick', {
          faceCount, hasPose: !!pose, gazeZone: zone, fps: Math.round(fps),
        })
      }

      maybeFire('multiple_faces', faceCount >= 2, now)
      maybeFire('face_not_visible', faceCount === 0, now)
      // Strengthen gaze: a single sustained off-center glance OR a scanning
      // rhythm (reading an off-screen surface) — the latter catches a second
      // screen even when window focus never changes.
      reader.push(zone ?? 'center', now)
      const offCenter = zone !== null && zone !== 'center'
      maybeFire('looking_away_sustained', offCenter || reader.isReading(), now)

      raf = requestAnimationFrame(tick)
    }

    Promise.all([createFaceLandmarker(), createFaceDetector()])
      .then(([lm, det]) => {
        if (cancelled) { lm.close?.(); det.close?.(); return }
        landmarker = lm as Model
        detector = det as Model
        // The camera track is attached + played lazily in tick() once the
        // publication exists — see ensureCameraAttached().
        raf = requestAnimationFrame(tick)
      })
      .catch((err) => {
        // Model/WASM load failed on this device. Leave signals EMPTY rather than
        // throwing an unhandled rejection; surface a non-PII warning so a future
        // device-class regression is observable instead of a silent dead loop.
        if (!cancelled) console.warn('[proctoring] vision models failed to load', err)
      })

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      if (attachedTrack) attachedTrack.detach(video)
      video.remove()
      landmarker?.close()
      detector?.close()
      setSignals(EMPTY)
    }
  }, [armed])

  return { signals }
}
