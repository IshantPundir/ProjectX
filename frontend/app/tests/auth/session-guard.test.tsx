import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'

const mockGetUser = vi.fn()
const mockRouterPush = vi.fn()
let authStateCallback: ((event: string) => void) | null = null
const mockOnAuthStateChange = vi.fn((cb) => {
  authStateCallback = cb
  return { data: { subscription: { unsubscribe: vi.fn() } } }
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockRouterPush }),
}))
vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    auth: {
      getUser: mockGetUser,
      onAuthStateChange: mockOnAuthStateChange,
    },
  }),
}))

import { SessionGuard } from '@/components/dashboard/SessionGuard'

describe('SessionGuard', () => {
  beforeEach(() => {
    mockGetUser.mockReset()
    mockRouterPush.mockReset()
    mockOnAuthStateChange.mockReset()
    mockOnAuthStateChange.mockImplementation((cb) => {
      authStateCallback = cb
      return { data: { subscription: { unsubscribe: vi.fn() } } }
    })
    authStateCallback = null
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
  })

  it('redirects to /login if getUser returns null user', async () => {
    mockGetUser.mockResolvedValueOnce({ data: { user: null }, error: null })
    render(<SessionGuard />)
    await waitFor(() => {
      expect(mockRouterPush).toHaveBeenCalledWith('/login')
    })
  })

  it('does not redirect when getUser returns a user', async () => {
    mockGetUser.mockResolvedValueOnce({
      data: { user: { id: 'u1', email: 'u@x' } },
      error: null,
    })
    render(<SessionGuard />)
    // Give the effect a chance to run.
    await new Promise((r) => setTimeout(r, 10))
    expect(mockRouterPush).not.toHaveBeenCalled()
  })

  it('redirects on SIGNED_OUT auth event', async () => {
    mockGetUser.mockResolvedValueOnce({
      data: { user: { id: 'u1' } },
      error: null,
    })
    render(<SessionGuard />)
    await waitFor(() => expect(authStateCallback).not.toBeNull())
    authStateCallback!('SIGNED_OUT')
    await waitFor(() => {
      expect(mockRouterPush).toHaveBeenCalledWith('/login')
    })
  })
})
