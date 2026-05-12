import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/api/client', () => ({
  apiFetch: vi.fn(),
}))

import * as client from '@/lib/api/client'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ats api wrappers', () => {
  it('listConnections calls GET /api/ats/connections', async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue([])
    const { listConnections } = await import('@/lib/api/ats')
    await listConnections('tok')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections',
      expect.objectContaining({ token: 'tok' }),
    )
  })

  it('createConnection POSTs the body', async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue({ id: 'x' })
    const { createConnection } = await import('@/lib/api/ats')
    await createConnection('tok', {
      vendor: 'ceipal',
      credentials: { email: 'u@x.com', password: 'p', api_key: 'k' },
    })
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('triggerManualSync POSTs to /sync', async () => {
    const mock = vi
      .mocked(client.apiFetch)
      .mockResolvedValue({ status: 'enqueued' })
    const { triggerManualSync } = await import('@/lib/api/ats')
    await triggerManualSync('tok', 'conn-123')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/sync',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
