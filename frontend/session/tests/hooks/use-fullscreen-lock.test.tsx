import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useFullscreenLock } from '@/hooks/use-fullscreen-lock'

function setFullscreen(el: Element | null) {
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => el })
}
function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
}
/** Simulate a browser that can (or cannot) enter element fullscreen. */
function setFullscreenSupported(supported: boolean) {
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, get: () => supported })
  Object.defineProperty(document.documentElement, 'requestFullscreen', {
    configurable: true,
    value: supported ? vi.fn().mockResolvedValue(undefined) : undefined,
  })
}

afterEach(() => {
  setFullscreen(null)
  setVisibility('visible')
  setFullscreenSupported(false)
})

describe('useFullscreenLock', () => {
  it('is unlocked when supported and not in fullscreen', () => {
    setFullscreenSupported(true)
    setFullscreen(null)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(false)
  })

  it('is locked when fullscreen and visible', () => {
    setFullscreenSupported(true)
    setFullscreen(document.documentElement)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(true)
  })

  it('recomputes to unlocked when fullscreen is exited', () => {
    setFullscreenSupported(true)
    setFullscreen(document.documentElement)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(true)
    act(() => {
      setFullscreen(null)
      document.dispatchEvent(new Event('fullscreenchange'))
    })
    expect(result.current.locked).toBe(false)
  })

  it('recomputes to unlocked when the tab is hidden (minimize / tab-switch)', () => {
    setFullscreenSupported(true)
    setFullscreen(document.documentElement)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(true)
    act(() => {
      setVisibility('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(result.current.locked).toBe(false)
  })

  it('degrades to locked (never blocks) when fullscreen is unsupported (e.g. iOS Safari)', () => {
    setFullscreenSupported(false)
    setFullscreen(null)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(true)
  })
})
