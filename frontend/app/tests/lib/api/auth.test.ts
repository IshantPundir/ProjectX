import { afterEach, describe, expect, it, vi } from 'vitest'

import { authApi, type MeResponse } from '@/lib/api/auth'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('authApi.me', () => {
  it('parses the full backend MeResponse shape', async () => {
    const payload: MeResponse = {
      user_id: 'u1',
      email: 'user@example.com',
      full_name: 'Alice Example',
      tenant_id: 't1',
      client_name: 'Example Co',
      is_super_admin: true,
      onboarding_complete: true,
      has_org_units: true,
      workspace_mode: 'single_company',
      assignments: [
        {
          org_unit_id: 'o1',
          org_unit_name: 'Root',
          role_name: 'Recruiter',
          permissions: ['read'],
        },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    const me = await authApi.me('tok')
    expect(me).toEqual(payload)
  })
})
