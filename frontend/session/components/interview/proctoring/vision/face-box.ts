/** A face bounding box in the video's INTRINSIC pixel space. */
export interface PixelBox {
  x: number
  y: number
  width: number
  height: number
}

/** On-screen box in CONTAINER pixel space (for absolute positioning). */
export interface ScreenBox {
  left: number
  top: number
  width: number
  height: number
}

/**
 * Map a face box from the video's intrinsic frame onto the on-screen container,
 * accounting for `object-cover` cropping (scale-to-fill, centered) and an
 * optional horizontal mirror (the selfie preview is flipped). Returns null for
 * degenerate inputs. Pure — unit-tested; the WebGL detection that produces the
 * box is verified manually.
 */
export function mapBoxToContainer(
  box: PixelBox,
  videoW: number,
  videoH: number,
  containerW: number,
  containerH: number,
  mirrored: boolean,
): ScreenBox | null {
  if (videoW <= 0 || videoH <= 0 || containerW <= 0 || containerH <= 0) return null
  // object-cover: one scale fills both axes; overflow is cropped, content centered.
  const scale = Math.max(containerW / videoW, containerH / videoH)
  const dispW = videoW * scale
  const dispH = videoH * scale
  const offsetX = (containerW - dispW) / 2
  const offsetY = (containerH - dispH) / 2
  let left = offsetX + box.x * scale
  const top = offsetY + box.y * scale
  const width = box.width * scale
  const height = box.height * scale
  if (mirrored) {
    // Reflect across the container's vertical centerline.
    left = containerW - (left + width)
  }
  return { left, top, width, height }
}

/** Face boxes (intrinsic px) + count from a MediaPipe FaceDetector VIDEO result. */
export interface FaceBoxes {
  count: number
  boxes: PixelBox[]
}

export function extractFaceBoxes(result: {
  detections?: Array<{
    boundingBox?: { originX: number; originY: number; width: number; height: number }
  }>
}): FaceBoxes {
  const dets = result.detections ?? []
  const boxes: PixelBox[] = []
  for (const d of dets) {
    const b = d.boundingBox
    if (b) boxes.push({ x: b.originX, y: b.originY, width: b.width, height: b.height })
  }
  return { count: boxes.length, boxes }
}
