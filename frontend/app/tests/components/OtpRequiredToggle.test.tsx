import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { renderWithProviders } from '@/tests/_utils/render'
import { OtpRequiredToggle } from '@/components/dashboard/tracker/CandidateKanbanColumn'
import { pipelinesApi } from '@/lib/api/pipelines'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(async () => 'tok'),
}))

describe('OtpRequiredToggle', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('reflects the initial state from pipeline data', () => {
    renderWithProviders(
      <OtpRequiredToggle jobId="j1" stageId="s1" initial={true} />,
    )
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('calls setStageOtpRequired with the new value on toggle', async () => {
    const spy = vi
      .spyOn(pipelinesApi, 'setStageOtpRequired')
      .mockResolvedValue({
        id: 'i1',
        job_posting_id: 'j1',
        source_template_id: null,
        source_template_name: null,
        pipeline_version: 1,
        stages: [],
        created_at: '',
        updated_at: '',
      })
    renderWithProviders(
      <OtpRequiredToggle jobId="j1" stageId="s1" initial={false} />,
    )
    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('tok', 'j1', 's1', true),
    )
  })
})
