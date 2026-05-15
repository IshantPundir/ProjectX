import { describe, expect, it } from 'vitest'

import { checkLegacyRedirect } from '@/proxy'

describe('checkLegacyRedirect', () => {
  it('redirects /candidates?jd=<uuid>&view=kanban to /tracker/<uuid>', () => {
    const u = new URL(
      'http://localhost:3000/candidates?jd=488d1ded-0990-4aca-8bf4-2b6e6287d08c&view=kanban',
    )
    const target = checkLegacyRedirect(u)
    expect(target?.pathname).toBe('/tracker/488d1ded-0990-4aca-8bf4-2b6e6287d08c')
    expect(target?.search).toBe('')
  })

  it('redirects /candidates?view=kanban (no jd) to /tracker', () => {
    const u = new URL('http://localhost:3000/candidates?view=kanban')
    const target = checkLegacyRedirect(u)
    expect(target?.pathname).toBe('/tracker')
  })

  it('redirects to /tracker (not /tracker/<garbage>) when jd is not a UUID', () => {
    const u = new URL(
      'http://localhost:3000/candidates?jd=https://evil.example.com&view=kanban',
    )
    const target = checkLegacyRedirect(u)
    expect(target?.pathname).toBe('/tracker')
  })

  it('returns null for /candidates without view=kanban', () => {
    const u = new URL('http://localhost:3000/candidates?jd=anything')
    expect(checkLegacyRedirect(u)).toBeNull()
  })

  it('returns null for unrelated paths', () => {
    expect(checkLegacyRedirect(new URL('http://localhost:3000/jobs'))).toBeNull()
    expect(checkLegacyRedirect(new URL('http://localhost:3000/tracker'))).toBeNull()
  })
})
