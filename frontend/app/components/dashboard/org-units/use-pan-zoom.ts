import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from 'react'

interface PanZoomOptions {
  minScale?: number
  maxScale?: number
  /** Multiplier applied per wheel notch. Default 1.1. */
  zoomStep?: number
}

interface SetViewArgs {
  tx: number
  ty: number
  scale: number
  /** When true, the consumer's CSS transition on `transform` should fire. */
  animate?: boolean
}

interface PanZoomApi {
  tx: number
  ty: number
  scale: number
  animating: boolean
  setView: (args: SetViewArgs) => void
  /** Multiplies the current scale, clamped, anchored at the supplied
   *  viewport-local point (default: wrapper center). Static (translate
   *  + scale) so the canvas point under the anchor stays under it. */
  zoomBy: (factor: number, anchor?: { x: number; y: number }) => void
  onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerUp: (e: ReactPointerEvent<HTMLDivElement>) => void
}

/**
 * Pan + zoom for a fixed-position wrapper. The wrapper renders the
 * background pane and the (transformed) viewport; pan is suppressed
 * when the pointer-down target is inside a `[data-node-card]` element
 * so node clicks still fire.
 */
export function usePanZoom(
  wrapperRef: RefObject<HTMLDivElement | null>,
  opts: PanZoomOptions = {},
): PanZoomApi {
  const minScale = opts.minScale ?? 0.25
  const maxScale = opts.maxScale ?? 2.5
  const zoomStep = opts.zoomStep ?? 1.1

  const [tx, setTx] = useState(0)
  const [ty, setTy] = useState(0)
  const [scale, setScale] = useState(1)
  const [animating, setAnimating] = useState(false)

  // Latest values for use inside event handlers without stale closures.
  const latest = useRef({ tx, ty, scale })
  // Mirror the latest state into a ref for use inside event handlers
  // (the wheel listener is attached imperatively to get passive: false,
  // so it doesn't see new closure values when state changes).
  useLayoutEffect(() => {
    latest.current = { tx, ty, scale }
  }, [tx, ty, scale])

  const panState = useRef<{
    pointerId: number
    startClientX: number
    startClientY: number
    startTx: number
    startTy: number
  } | null>(null)

  const setView = useCallback((args: SetViewArgs) => {
    setAnimating(args.animate === true)
    setTx(args.tx)
    setTy(args.ty)
    setScale(args.scale)
  }, [])

  const clampScale = useCallback(
    (s: number) => Math.max(minScale, Math.min(maxScale, s)),
    [minScale, maxScale],
  )

  const zoomBy = useCallback(
    (factor: number, anchor?: { x: number; y: number }) => {
      const wrapper = wrapperRef.current
      let ax = anchor?.x
      let ay = anchor?.y
      if (ax === undefined || ay === undefined) {
        const rect = wrapper?.getBoundingClientRect()
        ax = (rect?.width ?? 0) / 2
        ay = (rect?.height ?? 0) / 2
      }
      const cur = latest.current
      const newScale = clampScale(cur.scale * factor)
      const ratio = newScale / cur.scale
      // Keep the canvas point under (ax, ay) anchored:
      //   newTx = ax - ratio * (ax - tx)
      const newTx = ax - ratio * (ax - cur.tx)
      const newTy = ay - ratio * (ay - cur.ty)
      setAnimating(false)
      setScale(newScale)
      setTx(newTx)
      setTy(newTy)
    },
    [clampScale, wrapperRef],
  )

  // Wheel listener attached imperatively so we can pass passive: false.
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    function onWheel(e: WheelEvent) {
      e.preventDefault()
      const rect = el!.getBoundingClientRect()
      const ax = e.clientX - rect.left
      const ay = e.clientY - rect.top
      const factor = e.deltaY < 0 ? zoomStep : 1 / zoomStep
      const cur = latest.current
      const newScale = clampScale(cur.scale * factor)
      const ratio = newScale / cur.scale
      setAnimating(false)
      setScale(newScale)
      setTx(ax - ratio * (ax - cur.tx))
      setTy(ay - ratio * (ay - cur.ty))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [wrapperRef, clampScale, zoomStep])

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      // Only left-button drags initiate a pan.
      if (e.button !== 0) return
      const target = e.target as HTMLElement | null
      // If the press lands on a node card or any interactive overlay,
      // skip pan so the node's own click/contextmenu handlers fire.
      if (target?.closest('[data-node-card]')) return
      if (target?.closest('[data-no-pan]')) return
      panState.current = {
        pointerId: e.pointerId,
        startClientX: e.clientX,
        startClientY: e.clientY,
        startTx: latest.current.tx,
        startTy: latest.current.ty,
      }
      setAnimating(false)
      ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
    },
    [],
  )

  const onPointerMove = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      const ps = panState.current
      if (!ps) return
      const dx = e.clientX - ps.startClientX
      const dy = e.clientY - ps.startClientY
      setTx(ps.startTx + dx)
      setTy(ps.startTy + dy)
    },
    [],
  )

  const onPointerUp = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      const ps = panState.current
      if (!ps) return
      panState.current = null
      ;(e.currentTarget as HTMLElement).releasePointerCapture?.(ps.pointerId)
    },
    [],
  )

  return {
    tx,
    ty,
    scale,
    animating,
    setView,
    zoomBy,
    onPointerDown,
    onPointerMove,
    onPointerUp,
  }
}
