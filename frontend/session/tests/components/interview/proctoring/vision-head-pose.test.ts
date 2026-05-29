import { describe, expect, it } from 'vitest'
import { matrixToHeadPose } from '@/components/interview/proctoring/vision/head-pose'

// MediaPipe facialTransformationMatrixes[].data is a length-16,
// column-major 4x4 matrix.
const IDENTITY = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

describe('matrixToHeadPose', () => {
  it('returns ~zero angles for the identity matrix', () => {
    const p = matrixToHeadPose(IDENTITY)
    expect(Math.abs(p.yaw)).toBeLessThan(1)
    expect(Math.abs(p.pitch)).toBeLessThan(1)
    expect(Math.abs(p.roll)).toBeLessThan(1)
  })

  it('returns null-safe zero for a malformed matrix', () => {
    const p = matrixToHeadPose([1, 2, 3])
    expect(p).toEqual({ yaw: 0, pitch: 0, roll: 0 })
  })
})
