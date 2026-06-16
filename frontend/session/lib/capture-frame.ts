/**
 * Grab a high-quality still from a live <video> at its native resolution.
 * Drawn straight from the decoded frame, so the result is the TRUE camera
 * orientation (un-mirrored) even though the on-screen preview is CSS-mirrored --
 * correct for a report/ID-style photo. Encodes JPEG at q=0.92.
 */
export async function captureVideoFrame(video: HTMLVideoElement): Promise<Blob> {
  const w = video.videoWidth
  const h = video.videoHeight
  if (!w || !h) throw new Error('Video not ready for capture')
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas 2D context unavailable')
  ctx.drawImage(video, 0, 0, w, h)
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('Frame encode failed'))),
      'image/jpeg',
      0.92,
    )
  })
}
