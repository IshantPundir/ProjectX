'use client'

import Link from 'next/link'
import { toast } from 'sonner'

import { SessionStatusBadge } from '@/components/dashboard/candidates/SessionStatusBadge'
import { Button } from '@/components/px'
import type { AssignmentResponse } from '@/lib/api/candidates'
import type { SessionDetail, SessionState } from '@/lib/api/scheduler'
import { useAssignmentSessions } from '@/lib/hooks/use-assignment-sessions'
import { useCandidateAssignments } from '@/lib/hooks/use-candidate-assignments'
import { useResendInvite } from '@/lib/hooks/use-resend-invite'
import { useRevokeInvite } from '@/lib/hooks/use-revoke-invite'

interface Props {
  candidateId: string
}

// States where the token is still unused and the invite can still be
// resent / revoked. Once the session goes active or terminal, the token
// has been consumed and these actions are no longer applicable.
const PRE_ACTIVE_STATES: SessionState[] = ['created', 'pre_check', 'consented']

function isPreActive(state: SessionState): boolean {
  return PRE_ACTIVE_STATES.includes(state)
}

export default function CandidateSessionsTab({ candidateId }: Props) {
  const assignmentsQuery = useCandidateAssignments(candidateId)

  if (assignmentsQuery.isLoading) {
    return (
      <div className="bg-white border border-zinc-200 rounded-lg p-8 text-center">
        <p className="text-sm text-zinc-600">Loading sessions…</p>
      </div>
    )
  }

  if (assignmentsQuery.error) {
    return (
      <div className="bg-white border border-zinc-200 rounded-lg p-8 text-center">
        <p className="text-sm text-zinc-600">Failed to load assignments.</p>
      </div>
    )
  }

  const assignments = assignmentsQuery.data ?? []

  if (assignments.length === 0) {
    return (
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="text-center py-12 text-zinc-500">
          <p className="text-sm">No interview sessions yet.</p>
          <p className="text-xs mt-1">
            Assign this candidate to a JD first, then send an invite from the
            Assignments tab.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {assignments.map((assignment) => (
        <AssignmentSessionsBlock
          key={assignment.id}
          assignment={assignment}
          candidateId={candidateId}
        />
      ))}
    </div>
  )
}

interface AssignmentSessionsBlockProps {
  assignment: AssignmentResponse
  candidateId: string
}

function AssignmentSessionsBlock({ assignment, candidateId }: AssignmentSessionsBlockProps) {
  const sessionsQuery = useAssignmentSessions(assignment.id)
  const sessions = sessionsQuery.data?.items ?? []

  return (
    <section className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b border-zinc-200 bg-zinc-50 px-4 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-zinc-900">
            {assignment.job_title || 'Untitled job'}
          </h3>
          <p className="truncate text-xs text-zinc-500">
            Current stage: {assignment.current_stage_name || '—'}
          </p>
        </div>
        <span className="shrink-0 text-xs text-zinc-500">
          {sessions.length} session{sessions.length === 1 ? '' : 's'}
        </span>
      </header>

      {sessionsQuery.isLoading ? (
        <div className="p-6 text-center text-sm text-zinc-500">
          Loading sessions…
        </div>
      ) : sessionsQuery.error ? (
        <div className="p-6 text-center text-sm text-zinc-500">
          Failed to load sessions for this assignment.
        </div>
      ) : sessions.length === 0 ? (
        <div className="p-6 text-center text-sm text-zinc-500">
          No sessions yet. Send an invite from the Assignments tab.
        </div>
      ) : (
        <table className="min-w-full divide-y divide-zinc-200">
          <thead className="bg-white">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
              >
                Stage
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
              >
                Status
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
              >
                Created
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-zinc-600"
              >
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {sessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                candidateId={candidateId}
                jobTitle={assignment.job_title || 'Interview'}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

interface SessionRowProps {
  session: SessionDetail
  candidateId: string
  jobTitle: string
}

export function SessionRow({ session, candidateId, jobTitle }: SessionRowProps) {
  const resend = useResendInvite()
  const revoke = useRevokeInvite()
  const actionable = isPreActive(session.state)
  const pending = resend.isPending || revoke.isPending

  const handleResend = () => {
    resend.mutate(
      { sessionId: session.id },
      {
        onSuccess: () => toast.success('Invite resent'),
        onError: (err) => toast.error(err.message || 'Failed to resend invite'),
      },
    )
  }

  const handleRevoke = () => {
    revoke.mutate(
      { sessionId: session.id },
      {
        onSuccess: () => toast.success('Invite revoked'),
        onError: (err) => toast.error(err.message || 'Failed to revoke invite'),
      },
    )
  }

  const reportHref =
    `/reports/session/${session.id}` +
    `?candidateId=${encodeURIComponent(candidateId)}` +
    `&candidateName=${encodeURIComponent('')}` +
    `&title=${encodeURIComponent(jobTitle)}` +
    `&subtitle=${encodeURIComponent(session.stage_name || '')}`

  return (
    <tr className="hover:bg-zinc-50">
      <td className="px-4 py-2 text-sm text-zinc-900">
        {session.stage_name || '—'}
      </td>
      <td className="px-4 py-2 text-sm text-zinc-700">
        <SessionStatusBadge state={session.state} errorCode={null} />
      </td>
      <td className="px-4 py-2 text-sm text-zinc-700">
        {new Date(session.created_at).toLocaleString()}
      </td>
      <td className="px-4 py-2 text-right text-sm">
        <div className="inline-flex items-center gap-2">
          {session.state === 'completed' && (
            <Link
              href={reportHref}
              className="text-xs font-medium hover:underline"
              style={{ color: 'var(--px-accent)' }}
            >
              View report
            </Link>
          )}
          {actionable ? (
            <div className="inline-flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={handleResend}
              >
                {resend.isPending ? 'Resending…' : 'Resend'}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={handleRevoke}
              >
                {revoke.isPending ? 'Revoking…' : 'Revoke'}
              </Button>
            </div>
          ) : session.state === 'completed' ? null : (
            <span className="text-xs text-zinc-400">—</span>
          )}
        </div>
      </td>
    </tr>
  )
}
