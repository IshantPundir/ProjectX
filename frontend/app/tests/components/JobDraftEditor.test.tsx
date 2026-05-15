import { fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import { ApiError } from '@/lib/api/client'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

import { renderWithProviders } from '../_utils/render'

// --- Hook mocks (capture invocations so the test can assert behavior) -------

const updateMutate = vi.fn(async (body: unknown) => body)
const enrichMutate = vi.fn(async () => ({ status: 'accepted' }))
const extractMutate = vi.fn(async () => ({ status: 'accepted' }))

vi.mock('@/lib/hooks/use-update-job-draft', () => ({
  useUpdateJobDraft: () => ({
    mutateAsync: updateMutate,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-trigger-enrich', () => ({
  useTriggerEnrich: () => ({
    mutateAsync: enrichMutate,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-extract-signals', () => ({
  useExtractSignals: () => ({
    mutateAsync: extractMutate,
    isPending: false,
  }),
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}))

// JobDraftEditor must be imported AFTER the mocks above so the hook
// references inside the module pick up the mocked versions.
import { JobDraftEditor } from '@/components/dashboard/jd-panels/JobDraftEditor'

// --- Test fixture ----------------------------------------------------------

function makeJob(
  overrides: Partial<JobPostingWithSnapshot> = {},
): JobPostingWithSnapshot {
  return {
    id: 'job-1',
    title: 'Sr. Backend Engineer',
    org_unit_id: 'unit-1',
    org_unit_name: 'Workato',
    created_by_email: 'recruiter@example.com',
    updated_by_email: null,
    status: 'draft',
    status_error: null,
    created_at: '2026-05-14T00:00:00Z',
    updated_at: '2026-05-14T00:00:00Z',
    signal_count: 0,
    needs_review_count: 0,
    source: 'native',
    external_id: null,
    external_status: null,
    description_raw: '',
    project_scope_raw: null,
    description_enriched: null,
    target_headcount: null,
    deadline: null,
    latest_snapshot: null,
    enrichment_status: 'idle',
    enrichment_error: null,
    is_confirmed: false,
    can_manage: true,
    profile_ready: true,
    employment_type: null,
    work_arrangement: null,
    location: null,
    salary_range_min: null,
    salary_range_max: null,
    salary_currency: null,
    travel_required: null,
    start_date_pref: null,
    ...overrides,
  }
}

beforeEach(() => {
  updateMutate.mockClear()
  enrichMutate.mockClear()
  extractMutate.mockClear()
})

// --- Tests -----------------------------------------------------------------

describe('JobDraftEditor', () => {
  it('renders header, raw JD textarea, project scope, and both action buttons', () => {
    const { getByText, getByPlaceholderText, getByRole } = renderWithProviders(
      <JobDraftEditor job={makeJob()} />,
    )
    expect(getByText('Sr. Backend Engineer')).toBeTruthy()
    expect(getByText(/Workato/)).toBeTruthy()
    expect(getByPlaceholderText('Paste the job description here…')).toBeTruthy()
    expect(getByRole('button', { name: /Enrich JD/i })).toBeTruthy()
    expect(getByRole('button', { name: /Extract signals/i })).toBeTruthy()
  })

  it('shows the ATS provenance chip when source != native', () => {
    const { getByText } = renderWithProviders(
      <JobDraftEditor
        job={makeJob({ source: 'ats_ceipal', description_raw: 'Pre-filled' })}
      />,
    )
    expect(getByText('from ATS')).toBeTruthy()
  })

  it('disables both action buttons when description_raw is empty', () => {
    const { getByRole } = renderWithProviders(
      <JobDraftEditor job={makeJob()} />,
    )
    expect(
      getByRole('button', { name: /Enrich JD/i }).hasAttribute('disabled'),
    ).toBe(true)
    expect(
      getByRole('button', { name: /Extract signals/i }).hasAttribute('disabled'),
    ).toBe(true)
  })

  it('PATCHes description_raw on textarea blur', async () => {
    const { getByPlaceholderText } = renderWithProviders(
      <JobDraftEditor job={makeJob()} />,
    )
    const ta = getByPlaceholderText('Paste the job description here…')
    fireEvent.change(ta, { target: { value: 'Full JD body here' } })
    fireEvent.blur(ta)
    await waitFor(() => expect(updateMutate).toHaveBeenCalled())
    expect(updateMutate).toHaveBeenCalledWith({
      description_raw: 'Full JD body here',
    })
  })

  it('clicking Enrich JD dispatches triggerEnrich (after flushing pending raw-JD edit)', async () => {
    const { getByPlaceholderText, getByRole } = renderWithProviders(
      <JobDraftEditor job={makeJob({ description_raw: 'Stale' })} />,
    )
    // Edit the textarea but don't blur — Enrich should flush it.
    const ta = getByPlaceholderText('Paste the job description here…')
    fireEvent.change(ta, { target: { value: 'Updated JD body' } })

    fireEvent.click(getByRole('button', { name: /Enrich JD/i }))
    await waitFor(() => expect(enrichMutate).toHaveBeenCalled())
    // Flushed PATCH first (with the just-typed value), then enrich.
    expect(updateMutate).toHaveBeenCalledWith({
      description_raw: 'Updated JD body',
    })
  })

  it('clicking Extract signals dispatches extractSignals (after flushing pending edit)', async () => {
    const { getByPlaceholderText, getByRole } = renderWithProviders(
      <JobDraftEditor job={makeJob({ description_raw: 'Stale' })} />,
    )
    const ta = getByPlaceholderText('Paste the job description here…')
    fireEvent.change(ta, { target: { value: 'Latest JD body' } })

    fireEvent.click(getByRole('button', { name: /Extract signals/i }))
    await waitFor(() => expect(extractMutate).toHaveBeenCalled())
    expect(updateMutate).toHaveBeenCalledWith({
      description_raw: 'Latest JD body',
    })
  })

  it('surfaces an empty_raw_jd 422 as an inline action banner', async () => {
    extractMutate.mockRejectedValueOnce(
      new ApiError('Raw JD is empty', 422, 'empty_raw_jd'),
    )
    const { getByPlaceholderText, getByRole, findByText } = renderWithProviders(
      <JobDraftEditor job={makeJob({ description_raw: 'Some content' })} />,
    )
    // Force the user to have content (so the button isn't disabled) but
    // the server still 422s — e.g. concurrent ATS sync cleared the row.
    fireEvent.change(
      getByPlaceholderText('Paste the job description here…'),
      { target: { value: 'Still here' } },
    )
    fireEvent.click(getByRole('button', { name: /Extract signals/i }))
    expect(await findByText('Add the JD first')).toBeTruthy()
  })

  it('surfaces a company_profile_incomplete 422 as a follow-up banner', async () => {
    // profile_ready=true means the proactive banner is suppressed; if the
    // server still 422s (race condition, stale frontend state), the
    // action-error banner is the fallback affordance.
    enrichMutate.mockRejectedValueOnce(
      new ApiError(
        'Org unit has no ancestor with a completed company profile',
        422,
        'company_profile_incomplete',
      ),
    )
    const { getByPlaceholderText, getByRole, findByText } = renderWithProviders(
      <JobDraftEditor job={makeJob({ description_raw: 'Content' })} />,
    )
    fireEvent.change(
      getByPlaceholderText('Paste the job description here…'),
      { target: { value: 'Full JD' } },
    )
    fireEvent.click(getByRole('button', { name: /Enrich JD/i }))
    expect(await findByText('Complete the company profile first')).toBeTruthy()
  })

  it('falls back to a generic banner for unmapped errors', async () => {
    enrichMutate.mockRejectedValueOnce(
      new ApiError('Redis down', 500, null),
    )
    const { getByPlaceholderText, getByRole, findByText } = renderWithProviders(
      <JobDraftEditor job={makeJob({ description_raw: 'X' })} />,
    )
    fireEvent.change(
      getByPlaceholderText('Paste the job description here…'),
      { target: { value: 'Full JD' } },
    )
    fireEvent.click(getByRole('button', { name: /Enrich JD/i }))
    expect(await findByText('Something went wrong')).toBeTruthy()
    expect(await findByText('Redis down')).toBeTruthy()
  })

  it('renders the enriched JD when description_enriched is present', () => {
    const { getByText } = renderWithProviders(
      <JobDraftEditor
        job={makeJob({
          description_raw: 'raw',
          description_enriched: '## Enriched version\n\nMuch better.',
          enrichment_status: 'completed',
        })}
      />,
    )
    expect(getByText(/## Enriched version/)).toBeTruthy()
    expect(getByText('ready')).toBeTruthy() // status badge
  })

  it('renders a fully blocked view (no editor, no actions) when profile_ready=false', () => {
    const { getByText, getByRole, queryByPlaceholderText, queryByRole } =
      renderWithProviders(
        <JobDraftEditor
          job={makeJob({
            description_raw: 'A perfectly fine JD',
            profile_ready: false,
          })}
        />,
      )
    // Banner + CTA appear.
    expect(getByText('Complete the company profile first')).toBeTruthy()
    expect(getByRole('link', { name: /Complete the profile/i })).toBeTruthy()
    // No editable inputs, no action buttons — the recruiter cannot
    // configure the JD until the profile is complete.
    expect(queryByPlaceholderText('Paste the job description here…')).toBeNull()
    expect(queryByRole('button', { name: /Enrich JD/i })).toBeNull()
    expect(queryByRole('button', { name: /Extract signals/i })).toBeNull()
  })

  it('blocked view surfaces ATS-prefilled raw JD read-only', () => {
    const { getByText } = renderWithProviders(
      <JobDraftEditor
        job={makeJob({
          source: 'ats_ceipal',
          description_raw: 'Pre-filled ATS body that has a unique phrase abcxyz',
          profile_ready: false,
        })}
      />,
    )
    expect(getByText(/Pre-filled raw JD \(read-only\)/i)).toBeTruthy()
    expect(getByText(/abcxyz/)).toBeTruthy()
  })

  it('shows the streaming state when enrichment is in flight', () => {
    const { getByText, queryByText, getAllByText } = renderWithProviders(
      <JobDraftEditor
        job={makeJob({
          description_raw: 'raw',
          description_enriched: null,
          enrichment_status: 'streaming',
        })}
      />,
    )
    // Both the empty-state placeholder and the in-flight button render
    // 'Enriching…' copy when the actor is running. The "running" badge
    // and disabled buttons are what's load-bearing.
    expect(getAllByText(/Enriching…/).length).toBeGreaterThanOrEqual(1)
    expect(queryByText('ready')).toBeNull()
    expect(getByText('running')).toBeTruthy()
  })
})
