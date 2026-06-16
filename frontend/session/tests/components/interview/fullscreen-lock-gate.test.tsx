import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FullscreenLockGate } from '@/app/interview/[token]/FullscreenLockGate'

function setFullscreen(el: Element | null) {
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => el })
}

afterEach(() => {
  setFullscreen(null)
  vi.restoreAllMocks()
})

describe('FullscreenLockGate', () => {
  it('renders children and shows the blocking gate when not in fullscreen', () => {
    setFullscreen(null)
    render(<FullscreenLockGate><div>pre-check body</div></FullscreenLockGate>)
    expect(screen.getByText('pre-check body')).toBeInTheDocument()
    expect(screen.getByRole('alertdialog', { name: /fullscreen/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /enter fullscreen/i })).toBeInTheDocument()
  })

  it('requests fullscreen on the button click', async () => {
    setFullscreen(null)
    const req = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: req })
    const user = userEvent.setup()
    render(<FullscreenLockGate><div>body</div></FullscreenLockGate>)
    await user.click(screen.getByRole('button', { name: /enter fullscreen/i }))
    expect(req).toHaveBeenCalledTimes(1)
  })

  it('does not show the gate when already in fullscreen', () => {
    setFullscreen(document.documentElement)
    render(<FullscreenLockGate><div>body</div></FullscreenLockGate>)
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByText('body')).toBeInTheDocument()
  })
})
