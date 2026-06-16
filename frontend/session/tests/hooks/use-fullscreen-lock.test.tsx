import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useFullscreenLock } from '@/hooks/use-fullscreen-lock'

function setFullscreen(el: Element | null) {
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => el })
}
function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
}

afterEach(() => {
  setFullscreen(null)
  setVisibility('visible')
})

describe('useFullscreenLock', () => {
  it('is unlocked when not in fullscreen', () => {
    setFullscreen(null)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(false)
  })

  it('is locked when fullscreen and visible', () => {
    setFullscreen(document.documentElement)
    setVisibility('visible')
    const { result } = renderHook(() => useFullscreenLock())
    expect(result.current.locked).toBe(true)
  })

  it('recomputes to unlocked when fullscreen is exited', () => {
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
})
