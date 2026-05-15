import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'

import type { JobPostingSummary } from '@/lib/api/jobs'
import type { KanbanBoardResponse } from '@/lib/api/candidates'

vi.mock('@/lib/hooks/use-kanban-board', () => ({
  useKanbanBoard: vi.fn(),
}))

import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { TrackerJobCard } from '@/components/dashboard/tracker/TrackerJobCard'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const JOB: JobPostingSummary = {
  id: 'job-123',
  title: 'Senior Backend Engineer',
  org_unit_id: 'ou',
  org_unit_name: 'Acme · Platform Team',
  created_by_email: null,
  updated_by_email: null,
  status: 'active',
  status_error: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-14T00:00:00Z',
  signal_count: 7,
  needs_review_count: 0,
  source: 'native',
  external_id: null,
  external_status: null,
  profile_ready: true,
}

const BOARD: KanbanBoardResponse = {
  job_posting_id: 'job-123',
  stages: [
    { stage_id: 's1', stage_name: 'Intake', position: 0, candidates: Array(3).fill({}) as never },
    { stage_id: 's2', stage_name: 'Phone', position: 1, candidates: Array(4).fill({}) as never },
    { stage_id: 's3', stage_name: 'AI Screen', position: 2, candidates: Array(2).fill({}) as never },
    { stage_id: 's4', stage_name: 'Human Iv', position: 3, candidates: Array(2).fill({}) as never },
    { stage_id: 's5', stage_name: 'Debrief', position: 4, candidates: Array(1).fill({}) as never },
  ],
}

describe('TrackerJobCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders title, org, status pill, stage counts, and total', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: BOARD,
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })

    // Title + org
    expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument()
    expect(screen.getByText(/Acme · Platform Team/)).toBeInTheDocument()
    // Status pill
    expect(screen.getByText('active')).toBeInTheDocument()
    // Per-stage labels
    expect(screen.getByText(/Intake/)).toBeInTheDocument()
    expect(screen.getByText(/Phone/)).toBeInTheDocument()
    expect(screen.getByText(/Debrief/)).toBeInTheDocument()
    // Total = 3+4+2+2+1 = 12
    expect(screen.getByText(/12/)).toBeInTheDocument()
    // Linked to detail
    expect(screen.getByRole('link')).toHaveAttribute('href', '/tracker/job-123')
    // Negative control: loaded state should show neither the loading shimmer
    // nor the empty-board copy.
    expect(screen.queryByTestId('tracker-card-bar-loading')).not.toBeInTheDocument()
    expect(screen.queryByText(/No candidates yet/i)).not.toBeInTheDocument()
  })

  it('renders an error footer when the kanban board fetch fails', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('boom'),
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })
    expect(screen.getByText(/Couldn’t load board/i)).toBeInTheDocument()
    // Should NOT fall through to the misleading "No candidates yet" copy.
    expect(screen.queryByText(/No candidates yet/i)).not.toBeInTheDocument()
  })

  it('renders the empty-board message when stages are empty', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: { job_posting_id: 'job-123', stages: [] } as KanbanBoardResponse,
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })
    expect(screen.getByText(/No candidates yet/i)).toBeInTheDocument()
  })

  it('renders a shimmer placeholder while the board is loading', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })
    expect(screen.getByTestId('tracker-card-bar-loading')).toBeInTheDocument()
  })
})
