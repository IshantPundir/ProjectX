import { describe, expect, it } from 'vitest'
import {
  mapBoxToContainer,
  extractFaceBoxes,
} from '@/components/interview/proctoring/vision/face-box'

describe('mapBoxToContainer', () => {
  it('maps a box 1:1 when video and container match (scale 1)', () => {
    const r = mapBoxToContainer({ x: 10, y: 20, width: 30, height: 40 }, 100, 100, 100, 100, false)
    expect(r).toEqual({ left: 10, top: 20, width: 30, height: 40 })
  })

  it('accounts for object-cover vertical crop when the container is wider', () => {
    // 100x100 video in 200x100 container → scale=max(2,1)=2; dispH=200; offsetY=-50.
    const r = mapBoxToContainer({ x: 10, y: 10, width: 20, height: 20 }, 100, 100, 200, 100, false)
    expect(r).toEqual({ left: 20, top: -30, width: 40, height: 40 })
  })

  it('mirrors horizontally when mirrored=true', () => {
    const r = mapBoxToContainer({ x: 10, y: 10, width: 20, height: 20 }, 100, 100, 100, 100, true)
    // unmirrored left=10,width=20 → mirrored left = 100-(10+20)=70
    expect(r).toEqual({ left: 70, top: 10, width: 20, height: 20 })
  })

  it('returns null for degenerate sizes', () => {
    expect(mapBoxToContainer({ x: 0, y: 0, width: 1, height: 1 }, 0, 100, 100, 100, false)).toBeNull()
    expect(mapBoxToContainer({ x: 0, y: 0, width: 1, height: 1 }, 100, 100, 0, 100, false)).toBeNull()
  })
})

describe('extractFaceBoxes', () => {
  it('extracts count + boxes from detections', () => {
    const r = extractFaceBoxes({
      detections: [
        { boundingBox: { originX: 1, originY: 2, width: 3, height: 4 } },
        { boundingBox: { originX: 5, originY: 6, width: 7, height: 8 } },
      ],
    })
    expect(r.count).toBe(2)
    expect(r.boxes[0]).toEqual({ x: 1, y: 2, width: 3, height: 4 })
    expect(r.boxes[1]).toEqual({ x: 5, y: 6, width: 7, height: 8 })
  })

  it('handles empty / missing detections', () => {
    expect(extractFaceBoxes({}).count).toBe(0)
    expect(extractFaceBoxes({ detections: [] }).count).toBe(0)
    expect(extractFaceBoxes({ detections: [{}] }).boxes).toEqual([])
  })
})
