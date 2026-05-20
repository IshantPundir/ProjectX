import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DevtoolsShield, DevtoolsBlockedOverlay } from '@/components/DevtoolsShield'

afterEach(() => vi.restoreAllMocks())

function dispatchKey(init: KeyboardEventInit): KeyboardEvent {
  const e = new KeyboardEvent('keydown', { cancelable: true, ...init })
  window.dispatchEvent(e)
  return e
}

describe('DevtoolsShield — shortcut blocking', () => {
  it('blocks F12', () => {
    render(<DevtoolsShield />)
    expect(dispatchKey({ key: 'F12' }).defaultPrevented).toBe(true)
  })

  it('blocks Ctrl+Shift+I (Windows/Linux devtools)', () => {
    render(<DevtoolsShield />)
    expect(dispatchKey({ key: 'I', ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(true)
  })

  it('blocks Cmd+Opt+I (macOS devtools)', () => {
    render(<DevtoolsShield />)
    expect(dispatchKey({ key: 'I', metaKey: true, altKey: true }).defaultPrevented).toBe(true)
  })

  it('blocks Ctrl+U (view-source)', () => {
    render(<DevtoolsShield />)
    expect(dispatchKey({ key: 'U', ctrlKey: true }).defaultPrevented).toBe(true)
  })

  it('blocks the context menu (right-click)', () => {
    render(<DevtoolsShield />)
    const e = new MouseEvent('contextmenu', { cancelable: true })
    window.dispatchEvent(e)
    expect(e.defaultPrevented).toBe(true)
  })

  it('does NOT block normal typing', () => {
    render(<DevtoolsShield />)
    expect(dispatchKey({ key: 'a' }).defaultPrevented).toBe(false)
    expect(dispatchKey({ key: 'Enter' }).defaultPrevented).toBe(false)
  })

  it('renders nothing while devtools is not detected', () => {
    const { container } = render(<DevtoolsShield />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('DevtoolsBlockedOverlay', () => {
  it('renders the blocking message', () => {
    render(<DevtoolsBlockedOverlay />)
    expect(screen.getByText('Developer tools detected')).toBeInTheDocument()
    expect(screen.getByText(/must be closed/i)).toBeInTheDocument()
  })
})
