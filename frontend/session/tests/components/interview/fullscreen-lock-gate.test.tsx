import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FullscreenLockGate } from '@/app/interview/[token]/FullscreenLockGate'

function setFullscreen(el: Element | null) {
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => el })
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
  setFullscreenSupported(false)
  vi.restoreAllMocks()
})

describe('FullscreenLockGate', () => {
  it('renders children and shows the blocking gate when supported and not in fullscreen', () => {
    setFullscreenSupported(true)
    setFullscreen(null)
    render(
      <FullscreenLockGate>
        <div>pre-check body</div>
      </FullscreenLockGate>,
    )
    expect(screen.getByText('pre-check body')).toBeInTheDocument()
    expect(screen.getByRole('alertdialog', { name: /fullscreen/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /enter fullscreen/i })).toBeInTheDocument()
  })

  it('requests fullscreen on the button click', async () => {
    setFullscreenSupported(true)
    setFullscreen(null)
    const req = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(document.documentElement, 'requestFullscreen', {
      configurable: true,
      value: req,
    })
    const user = userEvent.setup()
    render(
      <FullscreenLockGate>
        <div>body</div>
      </FullscreenLockGate>,
    )
    await user.click(screen.getByRole('button', { name: /enter fullscreen/i }))
    expect(req).toHaveBeenCalledTimes(1)
  })

  it('does not show the gate when already in fullscreen', () => {
    setFullscreenSupported(true)
    setFullscreen(document.documentElement)
    render(
      <FullscreenLockGate>
        <div>body</div>
      </FullscreenLockGate>,
    )
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByText('body')).toBeInTheDocument()
  })

  it('does NOT block (no gate) when fullscreen is unsupported, e.g. iOS Safari', () => {
    setFullscreenSupported(false)
    setFullscreen(null)
    render(
      <FullscreenLockGate>
        <div>pre-check body</div>
      </FullscreenLockGate>,
    )
    // Candidate must never be trapped on a device that can't enter fullscreen.
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByText('pre-check body')).toBeInTheDocument()
  })
})
