import type { GazeZone, HeadPose, IrisOffset, SignalQuality } from './types'

// --- Starting thresholds (tune via debug overlay, spec §11) ---
const YAW_OFF = 22 // deg: head clearly turned left/right
const PITCH_DOWN = 22 // deg: head clearly tilted down (phone/notes)
const PITCH_UP = -20 // deg: head clearly tilted up
const YAW_BORDERLINE = 10 // deg: small turn; let iris break the tie
const IRIS_STRONG = 0.6 // blendshape score: clear eye deviation
const BLINK_CUTOFF = 0.4 // averaged eyeBlink blendshape
const DARK_CUTOFF = 0.12 // normalized eye-region brightness
const GLARE_CUTOFF = 0.7 // normalized specular brightness in eye region

/**
 * Head-pose-PRIMARY gaze zone with iris as a tie-breaker (spec §7②, D5).
 * Iris is only consulted when the head pose is borderline, so glasses-
 * corrupted iris cannot, on its own, manufacture an off-screen verdict.
 */
export function classifyGazeZone(pose: HeadPose, iris: IrisOffset): GazeZone {
  if (pose.pitch >= PITCH_DOWN) return 'down_away'
  if (pose.pitch <= PITCH_UP) return 'up'
  if (pose.yaw <= -YAW_OFF) return 'left'
  if (pose.yaw >= YAW_OFF) return 'right'
  // Borderline head turn: let a strong iris deviation decide direction.
  if (Math.abs(pose.yaw) >= YAW_BORDERLINE) {
    if (iris.out >= IRIS_STRONG || iris.in >= IRIS_STRONG) {
      return pose.yaw > 0 ? 'right' : 'left'
    }
  }
  return 'center'
}

export function eyeAspectRatio(blinkLeft: number, blinkRight: number): number {
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
