import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { ReelCard } from '@/components/dashboard/reports/ReelCard'
import type { ReelPlayback } from '@/lib/api/reels'

const READY: ReelPlayback = {
  status: 'ready',
  signed_url: 'https://x/reel.mp4',
  expires_at: null,
  duration_seconds: 30,
  chapters: [],
  generation_error: null,
  eligible: true,
  ineligible_reason: null,
  version: 1,
}

// Mock at the API/data boundary (composition-test convention): real ReelCard +
// real ReelTheater, only the data hook is stubbed.
vi.mock('@/lib/hooks/use-reel', () => ({
  useReel: () => ({ data: READY, isLoading: false }),
  useGenerateReel: () => ({ mutate: vi.fn(), isPending: false }),
}))

beforeAll(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('ReelCard reel playback', () => {
  // Regression: closing the reel and reopening it used to flash the player open
  // then immediately animate it shut (data-closing stuck 'true'), requiring a
  // page refresh. Root cause: ReelTheater was mounted persistently, so its
  // fire-once `closing` state never reset on reopen.
  it('reopening the reel after closing renders it cleanly (not stuck in the exit state)', async () => {
    const user = userEvent.setup()
    render(<ReelCard sessionId="s1" candidateName="Aarav" verdict="advance" />)
    const playLabel = /Play Aarav's Highlights/i
    // The theater renders in a Base UI portal (document.body), a tick after click.
    const shell = () => document.querySelector('.theater-shell')

    // open
    await user.click(screen.getByRole('button', { name: playLabel }))
    await waitFor(() => expect(shell()).toHaveAttribute('data-closing', 'false'))

    // close via the ✕; wait out the exit animation so onClose fires (playing -> false)
    await user.click(screen.getByRole('button', { name: /^Close$/ }))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 400))
    })

    // reopen — must come up clean, not stuck mid-exit (data-closing='true' was the bug)
    await user.click(screen.getByRole('button', { name: playLabel }))
    await waitFor(() => {
      expect(shell()).not.toBeNull()
      expect(shell()).toHaveAttribute('data-closing', 'false')
    })
  })
})
