'use client'

import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
      <div>
        <div className="mb-4 text-sm text-zinc-500">
          <Link href="/candidates" className="hover:underline">
            Candidates
          </Link>
        </div>
        <div className="text-sm text-zinc-500">Loading…</div>
      </div>
    )
  }

  if (error || !candidate) {
    return (
      <div>
        <div className="mb-4 text-sm text-zinc-500">
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
    <div>
      {/* Breadcrumb */}
      <div className="mb-4 text-sm text-zinc-500">
        <Link href="/candidates" className="hover:underline">
          ← Candidates
        </Link>
        <span className="mx-1.5">/</span>
        <span className="text-zinc-700">{displayName}</span>
      </div>

      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">{displayName}</h1>
        <p className="mt-1 text-sm text-zinc-500">
          <span>{candidate.email ?? '—'}</span>
          <span className="mx-2 text-zinc-300">·</span>
          <span>
            Created {new Date(candidate.created_at).toLocaleDateString()}
          </span>
        </p>
      </div>

      {/* Redaction notice */}
      {isRedacted && (
        <div className="mb-6">
          <Alert>
            <AlertTitle>PII redacted</AlertTitle>
            <AlertDescription>
              This candidate&apos;s PII has been removed via GDPR redaction.
              Audit trail preserved.
            </AlertDescription>
          </Alert>
        </div>
      )}

      {/* Tab switcher */}
      <div
        className="mb-6 inline-flex items-center rounded-lg border border-zinc-200 bg-white p-0.5"
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
              className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
                active
                  ? 'bg-zinc-100 text-zinc-900 font-medium'
                  : 'text-zinc-600 hover:text-zinc-900'
              }`}
            >
              {tab.label}
            </button>
          )
        })}
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
