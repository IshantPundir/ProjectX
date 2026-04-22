'use client'

import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/px'
import { useCandidate } from '@/lib/hooks/use-candidate'

import CandidateAssignmentsTab from './CandidateAssignmentsTab'
import CandidateProfileTab from './CandidateProfileTab'
import CandidateSessionsTab from './CandidateSessionsTab'

type TabKey = 'profile' | 'assignments' | 'sessions'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'profile', label: 'Profile' },
  { key: 'assignments', label: 'Assignments' },
  { key: 'sessions', label: 'Sessions' },
]

function normalizeTab(raw: string | null): TabKey {
  if (raw === 'assignments' || raw === 'sessions') return raw
  return 'profile'
}

export default function CandidateDetailPage() {
  const params = useParams<{ candidateId: string }>()
  const candidateId = params.candidateId
  const searchParams = useSearchParams()
  const router = useRouter()

  const activeTab = normalizeTab(searchParams.get('tab'))

  const updateTab = useCallback(
    (tab: TabKey) => {
      const next = new URLSearchParams(searchParams.toString())
      if (tab === 'profile') next.delete('tab')
      else next.set('tab', tab)
      const qs = next.toString()
      router.replace(
        `/candidates/${candidateId}${qs ? `?${qs}` : ''}`,
        { scroll: false },
      )
    },
    [searchParams, router, candidateId],
  )

  const { data: candidate, isLoading, error } = useCandidate(candidateId)

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1100px] px-8 pb-10 pt-5">
        <div
          className="mb-4 text-[12px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          <Link href="/candidates" className="hover:underline">
            ← Candidates
          </Link>
        </div>
        <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>Loading…</div>
      </div>
    )
  }

  if (error || !candidate) {
    return (
      <div className="mx-auto max-w-[1100px] px-8 pb-10 pt-5">
        <div
          className="mb-4 text-[12px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          <Link href="/candidates" className="hover:underline">
            ← Candidates
          </Link>
        </div>
        <Alert variant="destructive">
          <AlertTitle>Could not load candidate</AlertTitle>
          <AlertDescription>
            {error instanceof Error
              ? error.message
              : 'Candidate not found or access denied.'}
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  const isRedacted = candidate.pii_redacted_at !== null
  const displayName = candidate.name ?? '(redacted)'

  return (
    <div className="mx-auto max-w-[1100px] px-8 pb-10 pt-5">
      {/* Breadcrumb */}
      <div
        className="mb-3 text-[12px]"
        style={{ color: 'var(--px-fg-3)' }}
      >
        <Link href="/candidates" className="hover:underline">
          ← Candidates
        </Link>
      </div>

      {/* Header */}
      <div className="mb-5">
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          {displayName}
        </h1>
        <p className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
          <span>{candidate.email ?? '—'}</span>
          <span className="mx-2" style={{ color: 'var(--px-fg-5)' }}>·</span>
          <span>
            Created {new Date(candidate.created_at).toLocaleDateString()}
          </span>
        </p>
      </div>

      {/* Redaction notice */}
      {isRedacted && (
        <div className="mb-5">
          <Alert variant="caution">
            <AlertTitle>PII redacted</AlertTitle>
            <AlertDescription>
              This candidate&apos;s PII has been removed via GDPR redaction.
              Audit trail preserved.
            </AlertDescription>
          </Alert>
        </div>
      )}

      {/* Tab bar */}
      <div
        className="mb-5 border-b"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <nav
          className="-mb-px flex gap-0"
          role="tablist"
          aria-label="Candidate detail sections"
        >
          {TABS.map((tab) => {
            const active = activeTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => updateTab(tab.key)}
                className="inline-flex items-center border-b-2 px-4 py-2.5 text-[13px] font-medium transition-colors"
                style={{
                  color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                  borderColor: active ? 'var(--px-accent)' : 'transparent',
                }}
              >
                {tab.label}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'profile' && (
          <CandidateProfileTab candidate={candidate} />
        )}
        {activeTab === 'assignments' && (
          <CandidateAssignmentsTab candidateId={candidateId} />
        )}
        {activeTab === 'sessions' && (
          <CandidateSessionsTab candidateId={candidateId} />
        )}
      </div>
    </div>
  )
}
