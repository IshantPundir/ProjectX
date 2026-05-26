import { afterEach, describe, expect, it, vi } from 'vitest'
import { reportsApi } from '@/lib/api/reports'

const PAGE = {
  items: [
    { session_id: 's1', candidate_id: 'c1', candidate_name: 'Punar', job_title: 'FDE',
      stage_name: 'New Stage', completed_at: '2026-05-24T00:00:00Z',
      report_status: 'ready', verdict: 'reject', overall_score: 36 },
  ],
  total: 1, offset: 0, limit: 50,
}

afterEach(() => vi.unstubAllGlobals())

describe('reportsApi.list', () => {
  it('GETs /api/reports and returns the page', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => PAGE } as Response)
    vi.stubGlobal('fetch', fetchMock)
    const page = await reportsApi.list('tok')
    expect(page.items[0].candidate_name).toBe('Punar')
    expect(fetchMock.mock.calls[0][0]).toContain('/api/reports')
  })
})
