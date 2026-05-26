import { afterEach, describe, expect, it, vi } from 'vitest'
import { reportsApi } from '@/lib/api/reports'
import { ApiError } from '@/lib/api/client'

const READY = {
  verdict: 'reject', verdict_reason: 'failed must-have', overall_score: 36,
  overall_coverage: 0.7, overall_confidence: 'medium', dimension_scores: {},
  knockout_results: [], signal_scorecards: [], question_scorecards: [],
  summary: { headline: 'h', strengths: [], gaps: [], rationale: '' },
  status: 'ready', id: 'r1', session_id: 's1', version: 1,
}

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response)
}

afterEach(() => vi.unstubAllGlobals())

describe('reportsApi.getBySession', () => {
  it('returns ready envelope on 200', async () => {
    vi.stubGlobal('fetch', mockFetch(200, READY))
    const env = await reportsApi.getBySession('tok', 's1')
    expect(env.state).toBe('ready')
    if (env.state === 'ready') expect(env.report.verdict).toBe('reject')
  })

  it('returns pending envelope on 202', async () => {
    vi.stubGlobal('fetch', mockFetch(202, { status: 'generating' }))
    const env = await reportsApi.getBySession('tok', 's1')
    expect(env).toEqual({ state: 'pending', status: 'generating' })
  })

  it('returns noReport envelope on 404 (does not throw)', async () => {
    vi.stubGlobal('fetch', mockFetch(404, { detail: 'Report not found' }))
    const env = await reportsApi.getBySession('tok', 's1')
    expect(env).toEqual({ state: 'noReport' })
  })

  it('throws ApiError on 403', async () => {
    vi.stubGlobal('fetch', mockFetch(403, { detail: 'Missing reports.view' }))
    await expect(reportsApi.getBySession('tok', 's1')).rejects.toBeInstanceOf(ApiError)
  })
})
