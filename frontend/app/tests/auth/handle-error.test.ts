/**
 * NOTE: `handleAuthError` uses a module-level `_redirectInFlight` boolean to
 * deduplicate concurrent 401s. Without resetting it between tests the lock
 * set by one test would bleed into the next. We call `_resetRedirectLockForTest`
 * (a test-only export) in `beforeEach` rather than `vi.resetModules()`, because
 * `resetModules` would break `instanceof ApiError` checks across the boundary
 * between the freshly-imported module and the test-created instances.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ApiError } from '@/lib/api/client'
import { handleAuthError, _resetRedirectLockForTest, type AppRouter } from '@/lib/auth/handle-error'

const mockSignOut = vi.fn()
const mockToast = vi.fn()

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ auth: { signOut: mockSignOut } }),
}))
vi.mock('sonner', () => ({
  toast: { error: (...args: unknown[]) => mockToast(...args) },
}))

describe('handleAuthError', () => {
  const router: AppRouter = { push: vi.fn() }

  beforeEach(() => {
    _resetRedirectLockForTest()
    mockSignOut.mockReset()
    mockSignOut.mockResolvedValue(undefined)
    mockToast.mockReset()
    ;(router.push as ReturnType<typeof vi.fn>).mockReset()
  })

  it('signs out, toasts, and redirects on 401 ApiError', async () => {
    const err = new ApiError('Unauthorized', 401)
    const matched = await handleAuthError(err, router)
    expect(matched).toBe(true)
    expect(mockSignOut).toHaveBeenCalledOnce()
    expect(mockToast).toHaveBeenCalledWith('Session expired. Please log in again.')
    expect(router.push).toHaveBeenCalledWith('/login')
  })

  it('matches "No active Supabase session" Error message', async () => {
    const err = new Error('No active Supabase session')
    const matched = await handleAuthError(err, router)
    expect(matched).toBe(true)
    expect(router.push).toHaveBeenCalledWith('/login')
  })

  it('no-op for unrelated errors', async () => {
    const err = new Error('Network timeout')
    const matched = await handleAuthError(err, router)
    expect(matched).toBe(false)
    expect(mockSignOut).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
  })

  it('ignores non-401 ApiErrors', async () => {
    const err = new ApiError('Server error', 500)
    const matched = await handleAuthError(err, router)
    expect(matched).toBe(false)
    expect(mockSignOut).not.toHaveBeenCalled()
  })
})
