import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'

import RecordingsLayout from '@/app/recordings/[token]/layout'

afterEach(() => {
  cleanup()
  document.body.removeAttribute('data-px-theme')
})

/**
 * Regression: the report theaters render in dialogs that createPortal to
 * document.body, escaping the in-tree daylight wrapper. On the candidate
 * session app <html> is data-px-theme="cool-light", so without scoping <body>
 * to daylight the portaled gauges/pills/proctoring-timeline lose their colors
 * (daylight-only --px-*-fill vars are undefined under cool-light).
 */
describe('RecordingsLayout — daylight scope for portaled content', () => {
  it('sets data-px-theme="daylight" on <body> while mounted', () => {
    expect(document.body.getAttribute('data-px-theme')).toBeNull()
    render(
      <RecordingsLayout>
        <div>child</div>
      </RecordingsLayout>,
    )
    expect(document.body.getAttribute('data-px-theme')).toBe('daylight')
  })

  it('restores the previous <body> theme on unmount (so other routes are unaffected)', () => {
    const { unmount } = render(
      <RecordingsLayout>
        <div>child</div>
      </RecordingsLayout>,
    )
    expect(document.body.getAttribute('data-px-theme')).toBe('daylight')
    unmount()
    expect(document.body.getAttribute('data-px-theme')).toBeNull()
  })
})
