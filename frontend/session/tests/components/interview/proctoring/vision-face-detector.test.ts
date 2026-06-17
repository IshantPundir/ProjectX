import { describe, expect, it } from 'vitest'
import { summarizeDetections } from '@/components/interview/proctoring/vision/face-detector'

describe('summarizeDetections', () => {
  it('returns 0 / 0 confidence for no detections', () => {
    expect(summarizeDetections({ detections: [] })).toEqual({ faceCount: 0, topConfidence: 0 })
    expect(summarizeDetections({})).toEqual({ faceCount: 0, topConfidence: 0 })
  })

  it('counts confident faces and reports the highest confidence', () => {
    const r = {
      detections: [
        { categories: [{ score: 0.62 }] },
        { categories: [{ score: 0.91 }] },
      ],
    }
    expect(summarizeDetections(r)).toEqual({ faceCount: 2, topConfidence: 0.91 })
  })

  it('does NOT count a low-confidence box (false positive) but still reports its confidence', () => {
    // 0.45 is below the floor — a shadow/pattern, not a real face. It must not
    // inflate faceCount (which would fire a spurious multiple_faces), though it
    // still informs topConfidence/quality.
    const r = {
      detections: [
        { categories: [{ score: 0.92 }] }, // the real candidate face
        { categories: [{ score: 0.45 }] }, // junk
      ],
    }
    expect(summarizeDetections(r)).toEqual({ faceCount: 1, topConfidence: 0.92 })
  })

  it('a single below-floor detection counts as zero faces', () => {
    expect(summarizeDetections({ detections: [{ categories: [{ score: 0.4 }] }] })).toEqual({
      faceCount: 0,
      topConfidence: 0.4,
    })
  })

  it('does NOT count a detection with a missing category score', () => {
    expect(summarizeDetections({ detections: [{}] })).toEqual({ faceCount: 0, topConfidence: 0 })
  })
})
