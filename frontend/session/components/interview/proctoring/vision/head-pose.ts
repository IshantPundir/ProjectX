import type { HeadPose } from './types'

const RAD2DEG = 180 / Math.PI

/**
 * Extract yaw/pitch/roll (degrees) from a MediaPipe facial transformation
 * matrix (length-16, column-major). Element (row,col) = data[col*4 + row].
 *
 * NOTE: axis signs depend on the model's coordinate convention — verify
 * direction live via the VisionDebugOverlay during tuning (spec §11) and
 * flip a sign here if needed. Defaults assume +yaw = head turned to the
 * candidate's right, +pitch = head tilted down.
 */
export function matrixToHeadPose(data: number[] | Float32Array): HeadPose {
  if (!data || data.length < 16) return { yaw: 0, pitch: 0, roll: 0 }
  const m = (row: number, col: number) => data[col * 4 + row]
  // Rotation submatrix R (3x3). Standard ZYX Euler extraction.
  const r00 = m(0, 0), r10 = m(1, 0), r20 = m(2, 0)
  const r21 = m(2, 1), r22 = m(2, 2)
  const yaw = Math.atan2(-r20, Math.hypot(r21, r22)) * RAD2DEG
  const pitch = Math.atan2(r21, r22) * RAD2DEG
  const roll = Math.atan2(r10, r00) * RAD2DEG
  return { yaw, pitch, roll }
}
