import { describe, expect, it, vi, beforeEach } from 'vitest'
import { reportsApi } from '@/lib/api/reports'
import * as client from '@/lib/api/client'

describe('reportsApi.share', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('POSTs the recipient email to the share endpoint', async () => {
    const spy = vi.spyOn(client, 'apiFetch').mockResolvedValue({ share_id: 's1', status: 'pending' })
    const res = await reportsApi.share('tok', 'sess-1', 'client@acme.com')
    expect(res).toEqual({ share_id: 's1', status: 'pending' })
    expect(spy).toHaveBeenCalledWith('/api/reports/session/sess-1/share', {
      token: 'tok',
      method: 'POST',
      body: JSON.stringify({ recipient_email: 'client@acme.com' }),
    })
  })
})
