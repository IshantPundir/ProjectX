import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/tests/_utils/render'
import { ReelCard } from '@/components/dashboard/reports/ReelCard'

// Mock the reel hooks at the API boundary.
vi.mock('@/lib/hooks/use-reel', () => ({
  useReel: () => ({
    data: { status: 'absent', eligible: true, ineligible_reason: null },
    isLoading: false,
  }),
  useGenerateReel: () => ({ mutate: vi.fn(), isPending: false }),
}))

describe('Highlights card', () => {
  it('labels the feature "Highlights", not "Highlight Reel"', () => {
    renderWithProviders(<ReelCard sessionId="s1" candidateName="Rahul" verdict="reject" />)
    // Header label must say exactly "Highlights"
    expect(screen.getByText('Highlights')).toBeInTheDocument()
    // No element should contain the old "Highlight Reel" name
    expect(screen.queryByText(/highlight reel/i)).not.toBeInTheDocument()
  })
})
