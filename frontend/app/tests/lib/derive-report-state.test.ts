import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api/client'
import { deriveReportState } from '@/lib/hooks/use-report'
import type { ReportRead } from '@/lib/api/reports'

const ready = (status: ReportRead['status']): ReportRead =>
  ({ verdict: 'reject', status } as ReportRead)

describe('deriveReportState', () => {
  it('loading while query is loading and no data', () => {
    expect(deriveReportState({ isLoading: true, data: undefined, error: null }).kind).toBe('loading')
  })
  it('forbidden on 403 error', () => {
    expect(deriveReportState({ isLoading: false, data: undefined, error: new ApiError('x', 403) }).kind).toBe('forbidden')
  })
  it('noReport on noReport envelope', () => {
    expect(deriveReportState({ isLoading: false, data: { state: 'noReport' }, error: null }).kind).toBe('noReport')
  })
  it('pending on pending envelope', () => {
    expect(deriveReportState({ isLoading: false, data: { state: 'pending', status: 'generating' }, error: null }).kind).toBe('pending')
  })
  it('failed when ready envelope has status failed', () => {
    expect(deriveReportState({ isLoading: false, data: { state: 'ready', report: ready('failed') }, error: null }).kind).toBe('failed')
  })
  it('ready when ready envelope has status ready', () => {
    const s = deriveReportState({ isLoading: false, data: { state: 'ready', report: ready('ready') }, error: null })
    expect(s.kind).toBe('ready')
    if (s.kind === 'ready') expect(s.report.verdict).toBe('reject')
  })
})
