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
      vendor: 'ats_ceipal',
      credentials: { email: 'u@x.com', password: 'p', api_key: 'k' },
    })
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('triggerManualSync POSTs to /sync with no body (single-trigger model)', async () => {
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

  it('resetCursor POSTs to /reset-cursor with reason', async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue(undefined)
    const { resetCursor } = await import('@/lib/api/ats')
    await resetCursor('tok', 'conn-123', 'manual-rescan')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/reset-cursor',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ reason: 'manual-rescan' }),
      }),
    )
  })

  it('updateStatusSyncMode PUTs the mode value', async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue(undefined)
    const { updateStatusSyncMode } = await import('@/lib/api/ats')
    await updateStatusSyncMode('tok', 'conn-123', 'mirror')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/connections/conn-123/status-sync-mode',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ mode: 'mirror' }),
      }),
    )
  })

  it('retryJobImport POSTs to /jobs/{job_id}/retry-import', async () => {
    const mock = vi.mocked(client.apiFetch).mockResolvedValue(undefined)
    const { retryJobImport } = await import('@/lib/api/ats')
    await retryJobImport('tok', 'job-456')
    expect(mock).toHaveBeenCalledWith(
      '/api/ats/jobs/job-456/retry-import',
      expect.objectContaining({ method: 'POST' }),
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
