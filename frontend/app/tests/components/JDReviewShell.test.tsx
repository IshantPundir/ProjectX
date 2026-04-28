import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent } from '@testing-library/react'
import { renderWithProviders } from '../_utils/render'
import { JDReviewShell } from '@/components/dashboard/jd-panels/JDReviewShell'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

// ---------------------------------------------------------------------------
// Next.js navigation mocks — JDReviewShell uses useRouter + useSearchParams
//
// We track the current search params in a module-level variable so that
// router.replace (which the component calls on tab click) drives a re-render
// by updating that variable. The mock calls a flush callback registered by
// the active render so the component re-reads from the updated params.
// ---------------------------------------------------------------------------

let _currentParams = new URLSearchParams()
let _onParamsChange: ((p: URLSearchParams) => void) | null = null

const mockReplace = vi.fn((url: string) => {
  // Extract query string from the URL the component passes to router.replace
  const qs = url.includes('?') ? url.split('?')[1] : ''
  _currentParams = new URLSearchParams(qs)
  _onParamsChange?.(_currentParams)
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => _currentParams,
}))

// Mutation hooks are not under test here — stub them out
vi.mock('@/lib/hooks/use-save-signals', () => ({
  useSaveSignals: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-confirm-signals', () => ({
  useConfirmSignals: () => ({ mutate: vi.fn(), isPending: false }),
}))

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

const mockJob: JobPostingWithSnapshot = {
  id: 'job-1',
  title: 'Test Role',
  org_unit_id: 'unit-1',
  description_raw: 'RAW_JD_BODY',
  description_enriched: 'ENRICHED_JD_BODY',
  status: 'signals_extracted',
  enrichment_status: 'completed',
  is_confirmed: false,
  can_manage: true,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  latest_snapshot: {
    version: 1,
    seniority_level: 'senior',
    role_summary: 'Test summary',
    signals: [
      {
        value: 'Python', type: 'competency', priority: 'required',
        weight: 3, knockout: true, stage: 'screen',
        evaluation_method: 'verbal_response',
        source: 'ai_extracted', inference_basis: null,
      },
    ],
  },
} as JobPostingWithSnapshot

// ---------------------------------------------------------------------------
// Reset between tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  _currentParams = new URLSearchParams()
  _onParamsChange = null
  mockReplace.mockClear()
})

describe('JDReviewShell — 3-way center toggle', () => {
  it('renders three tabs: Raw JD, Enriched JD, Signal details', () => {
    const { getByRole } = renderWithProviders(
      <JDReviewShell job={mockJob} onReEnrich={() => {}} />,
    )
    expect(getByRole('tab', { name: /Raw JD/i })).not.toBeNull()
    expect(getByRole('tab', { name: /Enriched JD/i })).not.toBeNull()
    expect(getByRole('tab', { name: /Signal details/i })).not.toBeNull()
  })

  it('hides the Enriched JD tab when description_enriched is null', () => {
    const skipped: JobPostingWithSnapshot = {
      ...mockJob,
      description_enriched: null,
      enrichment_status: 'idle',
    }
    const { queryByRole } = renderWithProviders(
      <JDReviewShell job={skipped} onReEnrich={() => {}} />,
    )
    expect(queryByRole('tab', { name: /Enriched JD/i })).toBeNull()
  })

  it('switches center body when a tab is clicked', () => {
    // Re-render the component after each router.replace to reflect the new URL
    const result = renderWithProviders(
      <JDReviewShell job={mockJob} onReEnrich={() => {}} />,
    )
    const { rerender } = result

    // Register a callback so mockReplace triggers a re-render with updated params
    _onParamsChange = () => {
      act(() => {
        rerender(<JDReviewShell job={mockJob} onReEnrich={() => {}} />)
      })
    }

    fireEvent.click(result.getByRole('tab', { name: /Raw JD/i }))
    expect(result.getByText(/RAW_JD_BODY/)).not.toBeNull()

    fireEvent.click(result.getByRole('tab', { name: /Enriched JD/i }))
    expect(result.getByText(/ENRICHED_JD_BODY/)).not.toBeNull()
  })
})
