import { describe, expect, it } from 'vitest'
import {
  classifyGazeZone,
  blinkScore,
  isBlinking,
  signalQuality,
  poseToGazePoint,
  eyeGazeOffset,
} from '@/components/interview/proctoring/vision/gaze'

const NO_EYE = { h: 0, v: 0 }

describe('eyeGazeOffset (signed eye direction)', () => {
  it('is neutral when no look blendshapes are active', () => {
    const e = eyeGazeOffset({
      inLeft: 0, outLeft: 0, upLeft: 0, downLeft: 0,
      inRight: 0, outRight: 0, upRight: 0, downRight: 0,
    })
    expect(e).toEqual({ h: 0, v: 0 })
  })
  it('reads positive h (candidate right): left eye IN + right eye OUT', () => {
    const e = eyeGazeOffset({
      inLeft: 0.8, outLeft: 0, upLeft: 0, downLeft: 0,
      inRight: 0, outRight: 0.8, upRight: 0, downRight: 0,
    })
    expect(e.h).toBeGreaterThan(0.5)
  })
  it('reads negative h (candidate left): left eye OUT + right eye IN', () => {
    const e = eyeGazeOffset({
      inLeft: 0, outLeft: 0.8, upLeft: 0, downLeft: 0,
      inRight: 0.8, outRight: 0, upRight: 0, downRight: 0,
    })
    expect(e.h).toBeLessThan(-0.5)
  })
  it('reads positive v (looking down)', () => {
    const e = eyeGazeOffset({
      inLeft: 0, outLeft: 0, upLeft: 0, downLeft: 0.8,
      inRight: 0, outRight: 0, upRight: 0, downRight: 0.8,
    })
    expect(e.v).toBeGreaterThan(0.5)
  })
})

describe('classifyGazeZone (effective = head pose + eye, co-primary)', () => {
  it('is center when head and eyes face the screen', () => {
    expect(classifyGazeZone({ yaw: 2, pitch: 1, roll: 0 }, NO_EYE)).toBe('center')
  })
  it('is left when head yaw is strongly negative', () => {
    expect(classifyGazeZone({ yaw: -30, pitch: 0, roll: 0 }, NO_EYE)).toBe('left')
  })
  it('is right when head yaw is strongly positive', () => {
    expect(classifyGazeZone({ yaw: 30, pitch: 0, roll: 0 }, NO_EYE)).toBe('right')
  })
  it('is down_away when head pitch is strongly down', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 28, roll: 0 }, NO_EYE)).toBe('down_away')
  })
  it('is down_away from EYES ALONE with a still head (phone-near-screen reading)', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 0, roll: 0 }, { h: 0, v: 0.9 })).toBe('down_away')
  })
  it('is right from a strong eye-look with a still head', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 0, roll: 0 }, { h: 0.9, v: 0 })).toBe('right')
  })
  it('head + eye add up: a small head turn plus an eye look crosses off-screen', () => {
    expect(classifyGazeZone({ yaw: 12, pitch: 0, roll: 0 }, { h: 0.5, v: 0 })).toBe('right')
  })
  it('stays center for small eye jitter with a neutral head (false-positive guard)', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 0, roll: 0 }, { h: 0.25, v: 0.2 })).toBe('center')
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

describe('poseToGazePoint (approximate dev pointer, mirrored selfie view)', () => {
  it('centers at 0.5,0.5 for a forward pose with neutral eyes', () => {
    expect(poseToGazePoint({ yaw: 0, pitch: 0, roll: 0 }, NO_EYE)).toEqual({ x: 0.5, y: 0.5 })
  })
  it('mirrors horizontally: positive yaw -> screen left, and down for positive pitch', () => {
    const p = poseToGazePoint({ yaw: 15, pitch: 12.5, roll: 0 }, NO_EYE)
    expect(p.x).toBeLessThan(0.5)
    expect(p.y).toBeGreaterThan(0.5)
  })
  it('mirrors horizontally: negative yaw -> screen right, and up for negative pitch', () => {
    const p = poseToGazePoint({ yaw: -15, pitch: -12.5, roll: 0 }, NO_EYE)
    expect(p.x).toBeGreaterThan(0.5)
    expect(p.y).toBeLessThan(0.5)
  })
  it('moves with the EYES on a still head (down-eyes -> lower screen)', () => {
    const p = poseToGazePoint({ yaw: 0, pitch: 0, roll: 0 }, { h: 0.9, v: 0.9 })
    expect(p.x).toBeLessThan(0.5) // eyes right -> screen left (mirrored)
    expect(p.y).toBeGreaterThan(0.5) // eyes down -> lower screen
  })
  it('clamps to [0,1] for extreme angles', () => {
    expect(poseToGazePoint({ yaw: 90, pitch: -90, roll: 0 }, NO_EYE)).toEqual({ x: 0, y: 0 })
  })
})
