import { describe, expect, it } from 'vitest'
import {
  classifyGazeZone,
  blinkScore,
  isBlinking,
  signalQuality,
} from '@/components/interview/proctoring/vision/gaze'

const ZERO_IRIS = { in: 0, out: 0, up: 0, down: 0 }

describe('classifyGazeZone (head-pose-primary)', () => {
  it('is center when looking straight at the screen', () => {
    expect(classifyGazeZone({ yaw: 2, pitch: 1, roll: 0 }, ZERO_IRIS)).toBe('center')
  })
  it('is left when yaw is strongly negative', () => {
    expect(classifyGazeZone({ yaw: -30, pitch: 0, roll: 0 }, ZERO_IRIS)).toBe('left')
  })
  it('is right when yaw is strongly positive', () => {
    expect(classifyGazeZone({ yaw: 30, pitch: 0, roll: 0 }, ZERO_IRIS)).toBe('right')
  })
  it('is down_away when pitch is strongly down (phone/notes tell)', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 28, roll: 0 }, ZERO_IRIS)).toBe('down_away')
  })
  it('escalates a borderline head pose to off-screen when iris agrees', () => {
    expect(classifyGazeZone({ yaw: 12, pitch: 0, roll: 0 }, { ...ZERO_IRIS, out: 0.8 })).toBe('right')
  })
  it('escalates a borderline negative head pose to left when iris agrees', () => {
    expect(classifyGazeZone({ yaw: -12, pitch: 0, roll: 0 }, { ...ZERO_IRIS, in: 0.8 })).toBe('left')
  })
  it('stays center on a borderline head pose when the iris signal is weak (false-positive guard)', () => {
    expect(classifyGazeZone({ yaw: 12, pitch: 0, roll: 0 }, { ...ZERO_IRIS, out: 0.3 })).toBe('center')
  })
})

describe('blinkScore / isBlinking', () => {
  it('blinkScore averages the two eye blink blendshapes', () => {
    expect(blinkScore(0.9, 0.7)).toBeCloseTo(0.8)
  })
  it('isBlinking is true above the blink threshold', () => {
    expect(isBlinking(0.6)).toBe(true)
    expect(isBlinking(0.1)).toBe(false)
  })
})

describe('signalQuality', () => {
  it('is unscorable when no face is detected', () => {
    expect(signalQuality({ faceConfidence: 0, brightness: 0.5, eyeGlare: 0 })).toBe('unscorable')
  })
  it('is low_light when the frame is too dark', () => {
    expect(signalQuality({ faceConfidence: 0.9, brightness: 0.05, eyeGlare: 0 })).toBe('low_light')
  })
  it('is glasses_degraded under strong eye-region glare', () => {
    expect(signalQuality({ faceConfidence: 0.9, brightness: 0.5, eyeGlare: 0.9 })).toBe('glasses_degraded')
  })
  it('is good in clean conditions', () => {
    expect(signalQuality({ faceConfidence: 0.9, brightness: 0.5, eyeGlare: 0.1 })).toBe('good')
  })
})
