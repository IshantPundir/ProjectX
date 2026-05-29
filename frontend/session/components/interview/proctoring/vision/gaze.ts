import type { GazeZone, HeadPose, SignalQuality } from './types'

// --- Starting thresholds (tune via debug overlay, spec §11) ---
const YAW_OFF = 22 // deg: head clearly turned left/right
const PITCH_DOWN = 22 // deg: head clearly tilted down (phone/notes)
const PITCH_UP = -20 // deg: head clearly tilted up
const BLINK_CUTOFF = 0.4 // averaged eyeBlink blendshape
const DARK_CUTOFF = 0.12 // normalized eye-region brightness
const GLARE_CUTOFF = 0.7 // normalized specular brightness in eye region

// Debug-overlay gaze-pointer mapping: degrees of head rotation that map to the
// screen edge (tune via the overlay). Approximate/uncalibrated by design.
const GAZE_YAW_RANGE = 30 // deg yaw -> half screen width
const GAZE_PITCH_RANGE = 25 // deg pitch -> half screen height

/**
 * Coarse gaze zone from HEAD POSE ONLY (spec §7②). The live plane is a coarse
 * deterrent; the accurate, eye-aware gaze is the server-side report model. No
 * iris here — it added fragility (glasses/noise) for accuracy we don't need
 * live. 'center' = on-screen.
 */
export function classifyGazeZone(pose: HeadPose): GazeZone {
  if (pose.pitch >= PITCH_DOWN) return 'down_away'
  if (pose.pitch <= PITCH_UP) return 'up'
  if (pose.yaw <= -YAW_OFF) return 'left'
  if (pose.yaw >= YAW_OFF) return 'right'
  return 'center'
}

/**
 * Average of the two eye-blink blendshape scores (0 = open, 1 = closed).
 * Named blinkScore because the inputs are MediaPipe blink blendshapes, NOT
 * the classical geometric Eye Aspect Ratio (which has the opposite polarity).
 */
export function blinkScore(blinkLeft: number, blinkRight: number): number {
  return (blinkLeft + blinkRight) / 2
}

export function isBlinking(ear: number): boolean {
  return ear >= BLINK_CUTOFF
}

export function signalQuality(args: {
  faceConfidence: number
  brightness: number
  eyeGlare: number
}): SignalQuality {
  if (args.faceConfidence <= 0) return 'unscorable'
  if (args.brightness < DARK_CUTOFF) return 'low_light'
  if (args.eyeGlare >= GLARE_CUTOFF) return 'glasses_degraded'
  return 'good'
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v
}

/**
 * Map head pose to an APPROXIMATE normalized point {x,y} in [0,1] for the dev
 * debug pointer ONLY (head-pose only; NOT calibrated). Horizontal is MIRRORED to
 * match the selfie self-view: head turned to the candidate's right -> x<0.5
 * (screen left). Down -> y>0.5.
 */
export function poseToGazePoint(pose: HeadPose): { x: number; y: number } {
  return {
    x: clamp01(0.5 - pose.yaw / (2 * GAZE_YAW_RANGE)),
    y: clamp01(0.5 + pose.pitch / (2 * GAZE_PITCH_RANGE)),
  }
}
