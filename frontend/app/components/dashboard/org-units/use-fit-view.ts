import { useCallback, useEffect, type RefObject } from 'react'

import type { LayoutNode } from './types'

interface FitViewOptions {
  /** Fraction of viewport to leave as breathing room. Default 0.2 (20%). */
  padding?: number
  /** Minimum scale clamp — matches usePanZoom's minScale. */
  minScale?: number
  /** Maximum scale clamp — matches usePanZoom's maxScale. */
  maxScale?: number
}

interface FitViewArgs<T extends object> {
  wrapperRef: RefObject<HTMLDivElement | null>
  nodes: LayoutNode<T>[]
  nodeWidth: number
  nodeHeight: number
  setView: (args: { tx: number; ty: number; scale: number; animate?: boolean }) => void
  /** Bumped each time consumers want a fit. Mount-time value: any. */
  runId: unknown
  options?: FitViewOptions
}

/**
 * Computes the bounding box of `nodes` (using uniform node dimensions),
 * then animates the viewport transform so the bbox is centred and fits
 * within the wrapper with `padding` breathing room. Re-runs whenever
 * `runId` reference-changes.
 */
export function useFitView<T extends object>({
  wrapperRef,
  nodes,
  nodeWidth,
  nodeHeight,
  setView,
  runId,
  options,
}: FitViewArgs<T>): () => void {
  const padding = options?.padding ?? 0.2
  const minScale = options?.minScale ?? 0.25
  const maxScale = options?.maxScale ?? 2.5

  const fit = useCallback(() => {
    const wrapper = wrapperRef.current
    if (!wrapper || nodes.length === 0) return
    const rect = wrapper.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return

    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const n of nodes) {
      minX = Math.min(minX, n.position.x)
      minY = Math.min(minY, n.position.y)
      maxX = Math.max(maxX, n.position.x + nodeWidth)
      maxY = Math.max(maxY, n.position.y + nodeHeight)
    }
    const bboxW = maxX - minX
    const bboxH = maxY - minY
    if (bboxW <= 0 || bboxH <= 0) return

    const usableW = rect.width * (1 - padding)
    const usableH = rect.height * (1 - padding)
    const rawScale = Math.min(usableW / bboxW, usableH / bboxH)
    const scale = Math.max(minScale, Math.min(maxScale, rawScale))

    const tx = (rect.width - bboxW * scale) / 2 - minX * scale
    const ty = (rect.height - bboxH * scale) / 2 - minY * scale

    setView({ tx, ty, scale, animate: true })
  }, [wrapperRef, nodes, nodeWidth, nodeHeight, setView, padding, minScale, maxScale])

  useEffect(() => {
    fit()
    // `runId` intentionally drives re-fits; `fit` already depends on
    // the inputs that affect the result.
  }, [runId, fit])

  return fit
}
