import { describe, expect, it } from 'vitest'
import { summarizeDetections } from '@/components/interview/proctoring/vision/face-detector'

describe('summarizeDetections', () => {
  it('returns 0 / 0 confidence for no detections', () => {
    expect(summarizeDetections({ detections: [] })).toEqual({ faceCount: 0, topConfidence: 0 })
    expect(summarizeDetections({})).toEqual({ faceCount: 0, topConfidence: 0 })
  })

  it('counts faces and reports the highest confidence', () => {
    const r = {
      detections: [
        { categories: [{ score: 0.62 }] },
        { categories: [{ score: 0.91 }] },
      ],
    }
    expect(summarizeDetections(r)).toEqual({ faceCount: 2, topConfidence: 0.91 })
  })

  it('treats a detection with no category score as confidence 0', () => {
    expect(summarizeDetections({ detections: [{}] })).toEqual({ faceCount: 1, topConfidence: 0 })
  })
})
