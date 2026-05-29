/** Coarse gaze zones (spec §7②). 'center' = on-screen. */
export type GazeZone = 'center' | 'left' | 'right' | 'up' | 'down_away'

/** Per-session trust level for the gaze signal (spec §7 robustness). */
export type SignalQuality = 'good' | 'glasses_degraded' | 'low_light' | 'unscorable'

/** Head orientation in degrees (yaw=left/right, pitch=up/down, roll=tilt). */
export interface HeadPose {
  yaw: number
  pitch: number
  roll: number
}

/** Iris look-direction, each 0..1, from MediaPipe blendshapes. */
export interface IrisOffset {
  in: number
  out: number
  /** reserved: vertical iris not used in the lateral tie-break (vertical gaze is pitch-driven) */
  up: number
  /** reserved: vertical iris not used in the lateral tie-break (vertical gaze is pitch-driven) */
  down: number
}

/** One detection tick's distilled signals (what the hook exposes). */
export interface VisionSignals {
  faceCount: number
  pose: HeadPose | null
  gazeZone: GazeZone | null
  /** Approximate normalized {x,y} in [0,1] for the dev gaze pointer (uncalibrated). */
  gazePoint: { x: number; y: number } | null
  /** Recent gaze points (oldest→newest) for the dev fading-trail viz. */
  gazeTrail: { x: number; y: number }[]
  blinking: boolean
  earValue: number | null
  quality: SignalQuality
  fps: number
}
