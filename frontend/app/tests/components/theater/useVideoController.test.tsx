import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useVideoController } from '@/components/dashboard/reports/theater/useVideoController'
import type { PlaybackSeekApi } from '@/components/dashboard/reports/SessionPlayback'

function makeVideo(currentTime = 0): HTMLVideoElement {
  const v = document.createElement('video')
  // jsdom's currentTime is a no-op setter; define a controllable one.
  Object.defineProperty(v, 'currentTime', { value: currentTime, writable: true, configurable: true })
  return v
}

describe('useVideoController', () => {
  // Regression: the Base UI dialog portal remounts the <video> on every reopen.
  // The controller must re-attach its media listeners to the NEW element, or the
  // playhead, play state and (via duration) the timeline all freeze on reopen.
  it('re-binds listeners when the video element is replaced', () => {
    const seekRef = { current: null } as { current: PlaybackSeekApi | null }
    const onMs = vi.fn()
    const videoA = makeVideo()
    const { result, rerender } = renderHook(
      ({ v }: { v: HTMLVideoElement }) => useVideoController(v, true, 0, seekRef, onMs),
      { initialProps: { v: videoA } },
    )

    act(() => { videoA.dispatchEvent(new Event('play')) })
    expect(result.current.playing).toBe(true)

    // simulate the reopen: a brand-new element replaces the old one
    const videoB = makeVideo(12)
    rerender({ v: videoB })

    // initial sync reads the fresh (paused) element
    expect(result.current.playing).toBe(false)

    // events on the NEW element must drive state — proving listeners re-attached
    act(() => { videoB.dispatchEvent(new Event('play')) })
    expect(result.current.playing).toBe(true)

    act(() => { videoB.dispatchEvent(new Event('timeupdate')) })
    expect(result.current.currentSec).toBe(12)

    // and the seek API points at the new element
    expect(seekRef.current).not.toBeNull()
  })

  it('stops updating from a detached element after it is replaced', () => {
    const seekRef = { current: null } as { current: PlaybackSeekApi | null }
    const onMs = vi.fn()
    const videoA = makeVideo()
    const { result, rerender } = renderHook(
      ({ v }: { v: HTMLVideoElement }) => useVideoController(v, true, 0, seekRef, onMs),
      { initialProps: { v: videoA } },
    )
    const videoB = makeVideo()
    rerender({ v: videoB })

    // the old element is no longer wired up
    act(() => { videoA.dispatchEvent(new Event('play')) })
    expect(result.current.playing).toBe(false)
  })
})
