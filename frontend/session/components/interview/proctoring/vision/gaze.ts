import type { GazeZone, HeadPose, SignalQuality } from './types'

// --- Starting thresholds (tune via debug overlay, spec §11) ---
const YAW_OFF = 22 // deg: effective gaze clearly off to the left/right
const PITCH_DOWN = 22 // deg: effective gaze clearly down (phone/notes)
const PITCH_UP = -20 // deg: effective gaze clearly up
// Eye-gaze contribution: a full eye deviation (|component| ~1) adds this many
// degrees to the effective head angle, so a strong eye-look with a STILL head
// can alone cross the off-screen thresholds — this is what catches reading from
// a phone/notes placed near the screen with minimal head movement. NOTE: this
// makes eyes a CO-PRIMARY signal (revising the original head-pose-only stance,
// D5). Eye blendshapes are less reliable with glasses / low light; Plan B gates
// this on signal quality once real brightness/glare metrics exist.
const EYE_GAIN = 26
const BLINK_CUTOFF = 0.4 // averaged eyeBlink blendshape
const DARK_CUTOFF = 0.12 // normalized eye-region brightness
const GLARE_CUTOFF = 0.7 // normalized specular brightness in eye region

// Debug-overlay gaze-pointer mapping: degrees of effective rotation that map to
// the screen edge (tune via the overlay). Approximate/uncalibrated by design.
const GAZE_YAW_RANGE = 30 // deg -> half screen width
const GAZE_PITCH_RANGE = 25 // deg -> half screen height

/** Raw MediaPipe per-eye look blendshape scores (0..1 each). */
export interface EyeGazeScores {
  inLeft: number
  outLeft: number
  upLeft: number
  downLeft: number
  inRight: number
  outRight: number
  upRight: number
  downRight: number
}

/** Signed eye-gaze direction. h: + = candidate's right, − = left. v: + = down, − = up. ~[-1,1]. */
export interface EyeGaze {
  h: number
  v: number
}

/**
 * Combine MediaPipe per-eye look blendshapes into a SIGNED eye-gaze direction.
 * Left eye looking IN (toward the nose) = looking to the candidate's right;
 * right eye looking IN = looking to the left — hence the cross-combination.
 */
export function eyeGazeOffset(s: EyeGazeScores): EyeGaze {
  const right = (s.inLeft + s.outRight) / 2
  const left = (s.outLeft + s.inRight) / 2
  const down = (s.downLeft + s.downRight) / 2
  const up = (s.upLeft + s.upRight) / 2
  return { h: right - left, v: down - up }
}

/**
 * Coarse gaze zone from an EFFECTIVE gaze angle = head pose + eye deviation
 * (spec §7②). Eyes are co-primary, so a still head with darting eyes (reading a
 * phone/notes near the screen) still registers as off-screen. 'center' = on-screen.
 */
export function classifyGazeZone(pose: HeadPose, eye: EyeGaze): GazeZone {
  const yaw = pose.yaw + eye.h * EYE_GAIN
  const pitch = pose.pitch + eye.v * EYE_GAIN
  if (pitch >= PITCH_DOWN) return 'down_away'
  if (pitch <= PITCH_UP) return 'up'
  if (yaw <= -YAW_OFF) return 'left'
  if (yaw >= YAW_OFF) return 'right'
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
 * Map effective gaze (head pose + eye deviation) to an APPROXIMATE normalized
 * point {x,y} in [0,1] for the dev debug pointer ONLY (NOT calibrated; consistent
 * with the coarse model). Horizontal is MIRRORED to match the selfie self-view:
 * looking to the candidate's right -> x<0.5 (screen left). Down -> y>0.5.
 */
export function poseToGazePoint(pose: HeadPose, eye: EyeGaze): { x: number; y: number } {
  const yaw = pose.yaw + eye.h * EYE_GAIN
  const pitch = pose.pitch + eye.v * EYE_GAIN
  return {
    x: clamp01(0.5 - yaw / (2 * GAZE_YAW_RANGE)),
    y: clamp01(0.5 + pitch / (2 * GAZE_PITCH_RANGE)),
  }
}
