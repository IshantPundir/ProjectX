import { describe, expect, it } from 'vitest'
import { captureVideoFrame } from '@/lib/capture-frame'

describe('captureVideoFrame', () => {
  it('rejects when the video has no decoded frame yet', async () => {
    const video = document.createElement('video')
    // videoWidth/videoHeight are 0 in jsdom (no real frame).
    await expect(captureVideoFrame(video)).rejects.toThrow(/not ready/i)
  })
})
