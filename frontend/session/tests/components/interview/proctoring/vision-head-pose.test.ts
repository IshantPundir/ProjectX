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

  it('extracts ~30deg yaw from a Y-axis rotation, leaving pitch/roll ~0', () => {
    const c = Math.cos(Math.PI / 6), s = Math.sin(Math.PI / 6) // 30deg
    // Ry(30deg), column-major 4x4
    const data = [c, 0, -s, 0,  0, 1, 0, 0,  s, 0, c, 0,  0, 0, 0, 1]
    const p = matrixToHeadPose(data)
    expect(Math.abs(p.yaw)).toBeGreaterThan(28)
    expect(Math.abs(p.yaw)).toBeLessThan(32)
    expect(Math.abs(p.pitch)).toBeLessThan(1.5)
    expect(Math.abs(p.roll)).toBeLessThan(1.5)
  })

  it('extracts ~20deg pitch from an X-axis rotation, leaving yaw/roll ~0', () => {
    const c = Math.cos(Math.PI / 9), s = Math.sin(Math.PI / 9) // 20deg
    // Rx(20deg), column-major 4x4
    const data = [1, 0, 0, 0,  0, c, s, 0,  0, -s, c, 0,  0, 0, 0, 1]
    const p = matrixToHeadPose(data)
    expect(Math.abs(p.pitch)).toBeGreaterThan(18)
    expect(Math.abs(p.pitch)).toBeLessThan(22)
    expect(Math.abs(p.yaw)).toBeLessThan(1.5)
    expect(Math.abs(p.roll)).toBeLessThan(1.5)
  })
})
