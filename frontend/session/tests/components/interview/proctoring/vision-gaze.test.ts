import { describe, expect, it } from 'vitest'
import {
  classifyGazeZone,
  blinkScore,
  isBlinking,
  signalQuality,
  poseToGazePoint,
} from '@/components/interview/proctoring/vision/gaze'

describe('classifyGazeZone (head-pose only)', () => {
  it('is center when looking straight at the screen', () => {
    expect(classifyGazeZone({ yaw: 2, pitch: 1, roll: 0 })).toBe('center')
  })
  it('is left when yaw is strongly negative', () => {
    expect(classifyGazeZone({ yaw: -30, pitch: 0, roll: 0 })).toBe('left')
  })
  it('is right when yaw is strongly positive', () => {
    expect(classifyGazeZone({ yaw: 30, pitch: 0, roll: 0 })).toBe('right')
  })
  it('is down_away when pitch is strongly down (phone/notes tell)', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 28, roll: 0 })).toBe('down_away')
  })
  it('is up when pitch is strongly up', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: -28, roll: 0 })).toBe('up')
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

describe('poseToGazePoint (mirrored selfie view, head-pose only)', () => {
  it('centers at 0.5,0.5 for a forward pose', () => {
    expect(poseToGazePoint({ yaw: 0, pitch: 0, roll: 0 })).toEqual({ x: 0.5, y: 0.5 })
  })
  it('mirrors horizontally: positive yaw -> screen left, and down for positive pitch', () => {
    const p = poseToGazePoint({ yaw: 15, pitch: 12.5, roll: 0 })
    expect(p.x).toBeLessThan(0.5)
    expect(p.y).toBeGreaterThan(0.5)
  })
  it('mirrors horizontally: negative yaw -> screen right, and up for negative pitch', () => {
    const p = poseToGazePoint({ yaw: -15, pitch: -12.5, roll: 0 })
    expect(p.x).toBeGreaterThan(0.5)
    expect(p.y).toBeLessThan(0.5)
  })
  it('clamps to [0,1] for extreme poses', () => {
    expect(poseToGazePoint({ yaw: 90, pitch: -90, roll: 0 })).toEqual({ x: 0, y: 0 })
  })
})
