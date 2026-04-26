import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useRef } from 'react'

import { usePanZoom } from '@/components/dashboard/org-units/use-pan-zoom'

function setup() {
  return renderHook(() => {
    const ref = useRef<HTMLDivElement>(null)
    const pz = usePanZoom(ref, { minScale: 0.25, maxScale: 2.5 })
    return { ref, pz }
  })
}

describe('usePanZoom', () => {
  it('initialises at identity transform', () => {
    const { result } = setup()
    expect(result.current.pz.tx).toBe(0)
    expect(result.current.pz.ty).toBe(0)
    expect(result.current.pz.scale).toBe(1)
    expect(result.current.pz.animating).toBe(false)
  })

  it('setView updates tx/ty/scale and toggles animating', () => {
    const { result } = setup()
    act(() => {
      result.current.pz.setView({ tx: 10, ty: 20, scale: 0.5, animate: true })
    })
    expect(result.current.pz.tx).toBe(10)
    expect(result.current.pz.ty).toBe(20)
    expect(result.current.pz.scale).toBe(0.5)
    expect(result.current.pz.animating).toBe(true)
  })

  it('zoomBy clamps within [minScale, maxScale]', () => {
    const { result } = setup()
    act(() => {
      result.current.pz.zoomBy(100, { x: 0, y: 0 })
    })
    expect(result.current.pz.scale).toBe(2.5)
    act(() => {
      result.current.pz.zoomBy(0.0001, { x: 0, y: 0 })
    })
    expect(result.current.pz.scale).toBe(0.25)
  })

  it('zoomBy preserves the canvas point under the supplied anchor', () => {
    const { result } = setup()
    act(() => result.current.pz.setView({ tx: 100, ty: 50, scale: 1 }))
    // Canvas point currently under viewport coord (200, 100):
    //   cx = (200 - 100) / 1 = 100
    //   cy = (100 - 50) / 1 = 50
    act(() => result.current.pz.zoomBy(2, { x: 200, y: 100 }))
    expect(result.current.pz.scale).toBe(2)
    // After zoom, that same canvas point must still sit at (200, 100):
    //   200 = tx' + 2 * 100  ⇒  tx' = 0
    //   100 = ty' + 2 * 50   ⇒  ty' = 0
    expect(result.current.pz.tx).toBe(0)
    expect(result.current.pz.ty).toBe(0)
  })
})
