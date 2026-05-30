// frontend/app/tests/api/proctoring.test.ts
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { reportsApi } from '@/lib/api/reports'
import * as client from '@/lib/api/client'

describe('reportsApi.getProctoring', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('calls the proctoring endpoint with the token', async () => {
    const spy = vi.spyOn(client, 'apiFetch').mockResolvedValue({ status: 'absent', flagged_intervals: [] })
    await reportsApi.getProctoring('tok', 'sess-1')
    expect(spy).toHaveBeenCalledWith(
      '/api/reports/session/sess-1/proctoring',
      expect.objectContaining({ token: 'tok' }),
    )
  })
})
