import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  ApiValidationError,
  apiFetch,
} from '@/lib/api/client'

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

describe('ApiValidationError', () => {
  it('is thrown on 422 responses with a detail array', async () => {
    mockFetch(422, {
      detail: [
        { loc: ['body', 'email'], msg: 'value is not a valid email', type: 'value_error.email' },
        { loc: ['body', 'password'], msg: 'ensure this value has at least 8 characters', type: 'value_error.any_str.min_length' },
      ],
    })

    await expect(apiFetch('/api/anything', { method: 'POST' })).rejects.toSatisfy((err) => {
      return (
        err instanceof ApiValidationError &&
        err instanceof ApiError &&
        err.status === 422 &&
        err.fieldErrors.length === 2 &&
        err.fieldErrors[0].loc[1] === 'email'
      )
    })
  })

  it('still throws ApiError (not ApiValidationError) on 422 with string detail', async () => {
    mockFetch(422, { detail: 'not an array' })

    await expect(apiFetch('/api/anything', { method: 'POST' })).rejects.toSatisfy((err) => {
      return err instanceof ApiError && !(err instanceof ApiValidationError)
    })
  })

  it('throws plain ApiError on non-422 failures', async () => {
    mockFetch(400, { detail: 'bad request' })

    await expect(apiFetch('/api/anything')).rejects.toSatisfy((err) => {
      return err instanceof ApiError && !(err instanceof ApiValidationError) && err.status === 400
    })
  })

  it('sets a human-readable message joining field errors', async () => {
    mockFetch(422, {
      detail: [
        { loc: ['body', 'email'], msg: 'invalid email', type: 'x' },
        { loc: ['body', 'password'], msg: 'too short', type: 'y' },
      ],
    })

    await expect(apiFetch('/api/anything')).rejects.toThrow(/invalid email, too short/)
  })
})
