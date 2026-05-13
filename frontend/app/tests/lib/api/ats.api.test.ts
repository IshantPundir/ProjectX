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

  it('triggerManualSync POSTs to /sync with no body when no phases passed', async () => {
    const mock = vi
      .mocked(client.apiFetch)
      .mockResolvedValue({ status: 'enqueued', phases: null })
    const { triggerManualSync } = await import('@/lib/api/ats')
    await triggerManualSync('tok', 'conn-123')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/sync',
      expect.objectContaining({ method: 'POST', body: undefined }),
    )
  })

  it('triggerManualSync POSTs {phases: [...]} when phases passed', async () => {
    const mock = vi
      .mocked(client.apiFetch)
      .mockResolvedValue({ status: 'enqueued', phases: ['clients'] })
    const { triggerManualSync } = await import('@/lib/api/ats')
    await triggerManualSync('tok', 'conn-123', ['clients'])
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/sync',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ phases: ['clients'] }),
      }),
    )
  })

  it('triggerManualSync omits body when empty phases array passed', async () => {
    const mock = vi
      .mocked(client.apiFetch)
      .mockResolvedValue({ status: 'enqueued', phases: null })
    const { triggerManualSync } = await import('@/lib/api/ats')
    await triggerManualSync('tok', 'conn-123', [])
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/sync',
      expect.objectContaining({ method: 'POST', body: undefined }),
    )
  })
})

describe('listJobStatuses', () => {
  it('GETs /api/ats/connections/{id}/job-statuses', async () => {
    const mock = vi
      .mocked(client.apiFetch)
      .mockResolvedValue([
        { id: 1, name: 'Active' },
        { id: 4, name: 'Jobs Filled' },
      ])
    const { listJobStatuses } = await import('@/lib/api/ats')
    const out = await listJobStatuses('tok', 'conn-123')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/job-statuses',
      expect.objectContaining({ token: 'tok' }),
    )
    expect(out).toEqual([
      { id: 1, name: 'Active' },
      { id: 4, name: 'Jobs Filled' },
    ])
  })
})

describe('updateJobStatusFilter', () => {
  it('PUTs to /api/ats/connections/{id}/job-status-filter with renamed body', async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue(undefined)
    const { updateJobStatusFilter } = await import('@/lib/api/ats')
    await updateJobStatusFilter('tok', 'conn-123', {
      ids: [1, 8],
      names: ['Active', 'Reactivated'],
    })
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/job-status-filter',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          status_ids: [1, 8],
          names: ['Active', 'Reactivated'],
        }),
      }),
    )
  })
})
