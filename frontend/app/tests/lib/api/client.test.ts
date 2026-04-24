import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiFetch, ApiError } from '@/lib/api/client'

function mockFetchOnce(response: Response) {
  const fn = vi.fn().mockResolvedValueOnce(response)
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('apiFetch', () => {
  it('returns undefined for 204 No Content', async () => {
    mockFetchOnce(new Response(null, { status: 204 }))
    const result = await apiFetch<void>('/api/x', { token: 't' })
    expect(result).toBeUndefined()
  })

  it('threads the caller-provided signal into fetch', async () => {
    const fetchMock = mockFetchOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const ctrl = new AbortController()
    await apiFetch('/api/x', { token: 't', signal: ctrl.signal })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.signal).toBe(ctrl.signal)
  })

  it('throws ApiError with status on non-OK responses', async () => {
    mockFetchOnce(
      new Response(JSON.stringify({ detail: 'nope' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    // First call - for toMatchObject
    await expect(apiFetch('/api/x', { token: 't' })).rejects.toMatchObject({
      message: 'nope',
      status: 403,
    })
    // Mock another response for the second call
    mockFetchOnce(
      new Response(JSON.stringify({ detail: 'nope' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    // Second call - for toBeInstanceOf
    await expect(apiFetch('/api/x', { token: 't' })).rejects.toBeInstanceOf(ApiError)
  })
})
