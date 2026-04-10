import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SignalSnapshot } from '@/lib/api/jobs'
import { useJobEditStore } from '@/stores/job-edit'

import { ConfirmBar } from '@/components/dashboard/jd-panels/ConfirmBar'
import { SignalsPanelWrapper } from '@/components/dashboard/jd-panels/SignalsPanelWrapper'
import { StaleBanner } from '@/components/dashboard/jd-panels/StaleBanner'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockSaveMutate = vi.fn()
const mockConfirmMutate = vi.fn()

vi.mock('@/lib/hooks/use-save-signals', () => ({
  useSaveSignals: () => ({
    mutate: mockSaveMutate,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-confirm-signals', () => ({
  useConfirmSignals: () => ({
    mutate: mockConfirmMutate,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-trigger-enrich', () => ({
  useTriggerEnrich: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

function Wrapper({ children }: { children: React.ReactNode }) {
  const client = createQueryClient()
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const SNAPSHOT: SignalSnapshot = {
  version: 1,
  required_skills: [
    { value: 'TypeScript', source: 'ai_extracted', inference_basis: null },
  ],
  preferred_skills: [
    { value: 'React', source: 'ai_inferred', inference_basis: 'JD mentions frontend' },
  ],
  must_haves: [
    { value: '3+ years experience', source: 'ai_extracted', inference_basis: null },
  ],
  good_to_haves: [],
  min_experience_years: 3,
  seniority_level: 'mid',
  role_summary: 'A mid-level frontend engineer role.',
}

// ---------------------------------------------------------------------------
// Reset Zustand store between tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  useJobEditStore.setState({
    isEditing: false,
    draft: null,
    isDirty: false,
  })
  mockSaveMutate.mockReset()
  mockConfirmMutate.mockReset()
})

// ---------------------------------------------------------------------------
// Test 1: renders view mode by default with Edit Signals button
// ---------------------------------------------------------------------------

describe('SignalsPanelWrapper', () => {
  it('renders view mode by default with Edit Signals and Confirm Signals buttons', () => {
    render(
      <Wrapper>
        <SignalsPanelWrapper
          snapshot={SNAPSHOT}
          isConfirmed={false}
          canManage={true}
          jobId="job-1"
        />
      </Wrapper>,
    )

    expect(screen.getByText('Edit Signals')).toBeInTheDocument()
    expect(screen.getByText('Confirm Signals')).toBeInTheDocument()
  })

  // -------------------------------------------------------------------------
  // Test 2: toggles to edit mode on click
  // -------------------------------------------------------------------------

  it('toggles to edit mode on click', async () => {
    const user = userEvent.setup()

    render(
      <Wrapper>
        <SignalsPanelWrapper
          snapshot={SNAPSHOT}
          isConfirmed={false}
          canManage={true}
          jobId="job-1"
        />
      </Wrapper>,
    )

    await user.click(screen.getByText('Edit Signals'))

    expect(screen.getByText('Done Editing')).toBeInTheDocument()
    expect(screen.getByText('Save Signals')).toBeInTheDocument()
  })

  // -------------------------------------------------------------------------
  // Test 5: hides Edit Signals when canManage is false
  // -------------------------------------------------------------------------

  it('hides Edit Signals when canManage is false', () => {
    render(
      <Wrapper>
        <SignalsPanelWrapper
          snapshot={SNAPSHOT}
          isConfirmed={false}
          canManage={false}
          jobId="job-1"
        />
      </Wrapper>,
    )

    expect(screen.queryByText('Edit Signals')).not.toBeInTheDocument()
    expect(screen.queryByText('Confirm Signals')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Test 3: ConfirmBar shows correct states
// ---------------------------------------------------------------------------

describe('ConfirmBar', () => {
  it('shows Save Signals when isEditing is true', () => {
    render(
      <Wrapper>
        <ConfirmBar
          isEditing={true}
          isConfirmed={false}
          isSaving={false}
          isConfirming={false}
          onSave={vi.fn()}
          onConfirm={vi.fn()}
        />
      </Wrapper>,
    )

    expect(screen.getByText('Save Signals')).toBeInTheDocument()
  })

  it('shows Signals Confirmed when isConfirmed is true', () => {
    render(
      <Wrapper>
        <ConfirmBar
          isEditing={false}
          isConfirmed={true}
          isSaving={false}
          isConfirming={false}
          onSave={vi.fn()}
          onConfirm={vi.fn()}
        />
      </Wrapper>,
    )

    expect(screen.getByText('Signals Confirmed')).toBeInTheDocument()
  })

  it('shows Confirm Signals when both isEditing and isConfirmed are false', () => {
    render(
      <Wrapper>
        <ConfirmBar
          isEditing={false}
          isConfirmed={false}
          isSaving={false}
          isConfirming={false}
          onSave={vi.fn()}
          onConfirm={vi.fn()}
        />
      </Wrapper>,
    )

    expect(screen.getByText('Confirm Signals')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Test 4: StaleBanner renders when stale
// ---------------------------------------------------------------------------

describe('StaleBanner', () => {
  it('shows amber banner with Re-enrich JD button when stale', () => {
    render(
      <StaleBanner
        isStale={true}
        isEnriching={false}
        enrichmentError={null}
        onReEnrich={vi.fn()}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByText('Re-enrich JD')).toBeInTheDocument()
    expect(
      screen.getByText('Signals have been updated since the last enrichment.'),
    ).toBeInTheDocument()
  })

  it('shows blue Re-enriching pill when enriching', () => {
    render(
      <StaleBanner
        isStale={false}
        isEnriching={true}
        enrichmentError={null}
        onReEnrich={vi.fn()}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByText('Re-enriching JD...')).toBeInTheDocument()
  })

  it('shows red error banner with Retry button on enrichment error', () => {
    render(
      <StaleBanner
        isStale={false}
        isEnriching={false}
        enrichmentError="LLM timeout"
        onReEnrich={vi.fn()}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByText('LLM timeout')).toBeInTheDocument()
    expect(screen.getByText('Retry')).toBeInTheDocument()
  })

  it('renders nothing when not stale, not enriching, and no error', () => {
    const { container } = render(
      <StaleBanner
        isStale={false}
        isEnriching={false}
        enrichmentError={null}
        onReEnrich={vi.fn()}
        onRetry={vi.fn()}
      />,
    )

    expect(container.innerHTML).toBe('')
  })
})
