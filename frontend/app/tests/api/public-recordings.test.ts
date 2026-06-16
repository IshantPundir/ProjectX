import { afterEach, describe, expect, it, vi } from 'vitest'

import { reportsApi } from '@/lib/api/reports'

afterEach(() => vi.restoreAllMocks())

describe('reportsApi.getPublicRecordings', () => {
  it('GETs the public endpoint with NO Authorization header', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          candidate_name: 'A',
          job_title: 'R',
          stage_label: 'S',
          report: {},
          recording: { status: 'ready' },
          proctoring: { status: 'absent' },
          reel: { status: 'absent' },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    )

    const env = await reportsApi.getPublicRecordings('tok123')
    expect(env.candidate_name).toBe('A')

    const init = fetchSpy.mock.calls[0][1] as RequestInit
    const headers = new Headers(init?.headers)
    expect(headers.has('authorization')).toBe(false)
    expect(String(fetchSpy.mock.calls[0][0])).toContain('/api/public/recordings/tok123')
  })

  it('propagates a 404 as an ApiError (expired/revoked/unknown)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Not found' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await expect(reportsApi.getPublicRecordings('bad')).rejects.toThrow()
  })
})
