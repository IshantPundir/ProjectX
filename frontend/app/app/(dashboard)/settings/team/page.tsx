'use client'

import { useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { authApi, type MeResponse } from '@/lib/api/auth'
import { applyApiErrorToForm } from '@/lib/api/errors'
import {
  listConnections,
  listSyncLogs,
  triggerManualSync,
  type ATSConnection,
  type ATSSyncLog,
} from '@/lib/api/ats'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { useTeamMembers } from '@/lib/hooks/use-team-members'
import { useInviteTeamMember } from '@/lib/hooks/use-invite-team-member'
import { useResendTeamInvite } from '@/lib/hooks/use-resend-team-invite'
import { useRevokeTeamInvite } from '@/lib/hooks/use-revoke-team-invite'
import { useDeactivateUser } from '@/lib/hooks/use-deactivate-user'
import { isAnyAdmin } from '@/lib/hooks/use-me'
import type { TeamMember } from '@/lib/api/team'
import { DangerConfirmDialog } from '@/components/px'
import { AccessDenied } from '@/components/dashboard/AccessDenied'

import { inviteTeamMemberSchema, type InviteTeamMemberFormValues } from './schema'

/* ─── Icons ─── */

function IconUsers({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
    </svg>
  );
}

/* ─── Skeleton ─── */

function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr className="border-b border-zinc-100">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 bg-zinc-100 rounded animate-pulse" style={{ width: i === 0 ? "60%" : "40%" }} />
        </td>
      ))}
    </tr>
  );
}

function TableSkeleton({ cols, rows = 3 }: { cols: number; rows?: number }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-6">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-zinc-50 border-b border-zinc-200">
            {Array.from({ length: cols }).map((_, i) => (
              <th key={i} className="px-4 py-2.5">
                <div className="h-3 bg-zinc-200 rounded animate-pulse w-16" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, i) => (
            <SkeletonRow key={i} cols={cols} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ─── Confirmation dialog ─── */

interface ConfirmAction {
  title: string;
  message: string;
  confirmLabel: string;
  pendingLabel: string;
  onConfirm: () => Promise<void>;
}

/* ─── Display categorization ─── */

/** Three UI buckets derived from the new TeamMember boolean fields. The
 * underlying `source` is now provenance ('native' | 'ats_<vendor>'), not a
 * category — but the team page renders categories, so we derive. */
type TeamMemberCategory = 'user' | 'invite' | 'ats'

function memberCategory(m: TeamMember): TeamMemberCategory {
  if (m.has_auth_account) return 'user'
  if (m.invite_state === 'pending') return 'invite'
  return 'ats'
}

/* ─── Status badge ─── */

function StatusBadge({ member }: { member: TeamMember }) {
  const category = memberCategory(member)
  if (category === 'user') {
    const className = member.is_active
      ? 'bg-green-50 text-green-700'
      : 'bg-zinc-100 text-zinc-500'
    return (
      <span className={`px-2 py-0.5 rounded-full text-xs ${className}`}>
        {member.is_active ? 'Active' : 'Inactive'}
      </span>
    )
  }
  if (category === 'invite') {
    return (
      <span className="px-2 py-0.5 rounded-full text-xs bg-amber-50 text-amber-700">
        Invite pending
      </span>
    )
  }
  return (
    <span className="px-2 py-0.5 rounded-full text-xs bg-blue-50 text-blue-700">
      Imported from ATS
    </span>
  )
}

/* ─── Page ─── */

export default function TeamPage() {
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)
  // Tracks which ATS row is mid-invite so only that row's button spins.
  // Declared at the top alongside other hooks — must run on every render
  // (rules-of-hooks), so it can't sit below the non-admin early return.
  const [pendingInviteId, setPendingInviteId] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const membersQuery = useTeamMembers()
  const meQuery = useQuery<MeResponse>({
    queryKey: ['me'],
    queryFn: async () => authApi.me(await getFreshSupabaseToken()),
    staleTime: 60_000,
  })
  // Drives visibility of the "Sync users from ATS" button. We use the
  // first active connection — the per-(tenant, vendor) uniqueness on
  // ats_connections means there's at most one Ceipal hook today.
  const connectionsQuery = useQuery<ATSConnection[]>({
    queryKey: ['ats', 'connections'],
    queryFn: async () => listConnections(await getFreshSupabaseToken()),
    staleTime: 30_000,
  })

  // First *active* connection — only this one drives the sync button. A
  // disabled connection (revoked creds, error) is not actionable. Declared
  // here (above other hooks that depend on it) so the polling sync-logs
  // query can be enabled only when there's something to poll.
  const activeConnection = (connectionsQuery.data ?? []).find((c) => c.active)

  // Poll sync-logs at 2s while a run is in progress, 10s when idle. We
  // look at the MOST RECENT log only (sorted started_at desc). An older
  // 'running' row can linger when an earlier actor invocation crashed
  // without finalizing — `.some(l => running)` would lock the button on
  // those orphans. The latest row alone tells us whether *the current*
  // sync is in flight.
  const syncLogsQuery = useQuery<ATSSyncLog[]>({
    queryKey: ['ats', 'connection', activeConnection?.id, 'sync-logs'],
    queryFn: async () =>
      listSyncLogs(await getFreshSupabaseToken(), activeConnection!.id),
    enabled: !!activeConnection,
    refetchInterval: (query) => {
      const latest = query.state.data?.[0]
      return latest?.status === 'running' ? 2000 : 10000
    },
  })

  const isSyncRunning = syncLogsQuery.data?.[0]?.status === 'running'

  // When the latest sync transitions out of 'running', refresh the members
  // list. We track the previous "running" state in a ref so we only fire
  // the invalidate on the edge, not every poll tick.
  const wasRunningRef = useRef(false)
  useEffect(() => {
    if (wasRunningRef.current && !isSyncRunning) {
      queryClient.invalidateQueries({ queryKey: ['team', 'members'] })
      toast.success('ATS user sync finished.')
    }
    wasRunningRef.current = isSyncRunning
  }, [isSyncRunning, queryClient])

  const inviteMutation = useInviteTeamMember()
  const resendMutation = useResendTeamInvite()
  const revokeMutation = useRevokeTeamInvite()
  const deactivateMutation = useDeactivateUser()

  // One mutation per click target: invite-by-email button on an ATS row.
  // Reuses the same /api/settings/team/invite endpoint as the manual form —
  // on accept, the backend auto-links the resulting User row to the
  // ATSUserMapping in this tenant matching lower(email).
  const inviteFromAtsMutation = useMutation({
    mutationFn: async (email: string) => inviteMutation.mutateAsync({ email }),
    onSuccess: () => {
      // Refresh team list — the ATS row should disappear, replaced by an
      // 'Invite pending' row. inviteMutation already invalidates ['team',
      // 'members'] but we re-invalidate explicitly to keep this onSuccess
      // future-proof against the hook's internals changing.
      queryClient.invalidateQueries({ queryKey: ['team', 'members'] })
    },
  })

  // "Resync from ATS" — single-trigger sync; users get materialized as a
  // side-effect of jobs that reference them under the new job-driven
  // model. The actor enqueues asynchronously; we don't immediately
  // refresh members (that would race the worker). Instead, sync-logs
  // polling detects when the run finishes and the running→idle
  // transition effect above invalidates members.
  const syncATSUsersMutation = useMutation({
    mutationFn: async (connectionId: string) =>
      triggerManualSync(await getFreshSupabaseToken(), connectionId),
    onSuccess: () => {
      toast.success('Syncing from ATS…')
      // Kick the sync-logs query immediately so polling picks up the
      // new 'running' row this tick rather than waiting up to 10s.
      queryClient.invalidateQueries({
        queryKey: ['ats', 'connection', activeConnection?.id, 'sync-logs'],
      })
    },
    onError: () => toast.error('Could not trigger ATS user sync.'),
  })

  const form = useForm<InviteTeamMemberFormValues>({
    resolver: zodResolver(inviteTeamMemberSchema),
    defaultValues: { email: '' },
  })

  async function onInvite(values: InviteTeamMemberFormValues) {
    try {
      const result = await inviteMutation.mutateAsync({ email: values.email })
      form.reset()
      toast.success(
        result.invite_url ? `Invite sent! URL: ${result.invite_url}` : 'Invite sent!',
      )
    } catch (err) {
      if (applyApiErrorToForm(err, form, { fallbackFieldKey: 'email' })) return
      toast.error(err instanceof Error ? err.message : 'Failed to send invite')
    }
  }

  const me = meQuery.data ?? null
  const isSuperAdmin = me?.is_super_admin ?? false
  const showInviteForm = !meQuery.isLoading && isSuperAdmin

  // RBAC: super admin OR any-unit Admin. Wait for /me so we don't flicker.
  if (!meQuery.isLoading && !isAnyAdmin(me)) {
    return <AccessDenied />
  }

  const members: TeamMember[] = membersQuery.data ?? []
  const loading = membersQuery.isLoading || meQuery.isLoading

  const showSyncATSButton = isSuperAdmin && activeConnection !== undefined

  const isConfirmPending =
    deactivateMutation.isPending || revokeMutation.isPending

  return (
    <>
      <DangerConfirmDialog
        open={!!confirmAction}
        title={confirmAction?.title ?? 'Confirm action'}
        description={confirmAction?.message ?? ''}
        confirmLabel={confirmAction?.confirmLabel ?? 'Confirm'}
        pendingLabel={confirmAction?.pendingLabel}
        pending={isConfirmPending}
        onConfirm={() => {
          void (async () => {
            try {
              await confirmAction?.onConfirm()
            } catch (err) {
              toast.error(err instanceof Error ? err.message : 'Action failed')
            }
          })()
        }}
        onClose={() => setConfirmAction(null)}
      />

      <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-5">
        <div className="mb-6 flex items-start justify-between gap-3">
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Team &amp; access
          </h1>
          {showSyncATSButton && (
            <button
              type="button"
              onClick={() => syncATSUsersMutation.mutate(activeConnection.id)}
              disabled={syncATSUsersMutation.isPending || isSyncRunning}
              className="px-btn outline sm"
            >
              {syncATSUsersMutation.isPending
                ? 'Queuing…'
                : isSyncRunning
                  ? 'Syncing…'
                  : 'Sync users from ATS'}
            </button>
          )}
        </div>

        {showInviteForm && (
          <form
            onSubmit={form.handleSubmit(onInvite)}
            noValidate
            className="mb-6 rounded-[10px] border p-5"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <h2
              className="mb-3 text-[11px] font-semibold uppercase"
              style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
            >
              Invite team member
            </h2>
            <div className="flex items-end gap-3">
              <div className="flex-1">
                <label htmlFor="team-invite-email" className="px-label">Email</label>
                <input
                  id="team-invite-email"
                  type="email"
                  className="px-input"
                  placeholder="colleague@company.com"
                  {...form.register('email')}
                />
                {form.formState.errors.email && (
                  <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                    {form.formState.errors.email.message}
                  </p>
                )}
              </div>
              <button
                type="submit"
                disabled={form.formState.isSubmitting}
                className="px-btn primary sm"
              >
                {form.formState.isSubmitting ? 'Sending…' : 'Send invite'}
              </button>
            </div>
            <p className="px-hint">
              Roles and org unit assignments can be configured after the user joins.
            </p>
          </form>
        )}

      {loading ? (
        <>
          <div className="h-4 w-28 bg-zinc-100 rounded animate-pulse mb-3" />
          <TableSkeleton cols={isSuperAdmin ? 5 : 4} rows={4} />
        </>
      ) : members.length === 0 ? (
        <div className="flex flex-col items-center justify-center bg-white border border-dashed border-zinc-200 rounded-xl py-10 mb-6 text-center">
          <div className="w-10 h-10 rounded-full bg-zinc-100 flex items-center justify-center mb-3">
            <IconUsers className="w-5 h-5 text-zinc-400" />
          </div>
          <p className="text-sm font-medium text-zinc-600 mb-1">No team members yet</p>
          <p className="text-xs text-zinc-400">Invite a colleague or sync from your ATS to get started.</p>
        </div>
      ) : (
        <>
          <h2 className="text-sm font-medium text-zinc-900 mb-3">
            Team members ({members.length})
          </h2>
          <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden mb-6">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-zinc-50 border-b border-zinc-200">
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Email</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Name</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Role</th>
                    <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                    {isSuperAdmin && (
                      <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Actions</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => {
                    const category = memberCategory(m)
                    const externalRole = m.external_source_metadata?.role
                    return (
                    <tr
                      key={`${category}-${m.id}`}
                      className="border-b border-zinc-100 last:border-0"
                    >
                      <td className="px-4 py-2.5 text-zinc-900">{m.email}</td>
                      <td className="px-4 py-2.5 text-zinc-600">
                        {m.full_name || (category === 'ats' ? <span className="text-zinc-400 italic">—</span> : '—')}
                      </td>
                      <td className="px-4 py-2.5 text-zinc-600">
                        {m.is_super_admin ? (
                          <span className="bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded text-xs font-medium">
                            Super Admin
                          </span>
                        ) : m.assignments.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {m.assignments.map((a) => (
                              <span
                                key={`${a.org_unit_id}-${a.role_name}`}
                                className="bg-zinc-100 text-zinc-600 px-1.5 py-0.5 rounded text-xs"
                                title={a.org_unit_name}
                              >
                                {a.role_name}
                              </span>
                            ))}
                          </div>
                        ) : category === 'ats' && externalRole ? (
                          <span
                            className="bg-zinc-50 text-zinc-500 px-1.5 py-0.5 rounded text-xs"
                            title="Role from ATS"
                          >
                            {externalRole}
                          </span>
                        ) : (
                          <span className="text-zinc-400 italic">Unassigned</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        <StatusBadge member={m} />
                      </td>
                      {isSuperAdmin && (
                        <td className="px-4 py-2.5">
                          {/* User actions */}
                          {category === 'user' && !m.is_super_admin && m.is_active && (
                            <button
                              onClick={() =>
                                setConfirmAction({
                                  title: 'Deactivate user',
                                  message: `Deactivate ${m.email}? They will lose access to ProjectX.`,
                                  confirmLabel: 'Deactivate',
                                  pendingLabel: 'Deactivating…',
                                  onConfirm: async () => {
                                    await deactivateMutation.mutateAsync(m.id)
                                    setConfirmAction(null)
                                    toast.success(`${m.email} deactivated`)
                                  },
                                })
                              }
                              className="text-xs text-red-600 hover:text-red-700 hover:underline cursor-pointer transition-colors duration-150"
                            >
                              Deactivate
                            </button>
                          )}

                          {/* Invite actions */}
                          {category === 'invite' && (
                            <div className="flex items-center gap-2">
                              <button
                                onClick={async () => {
                                  try {
                                    await resendMutation.mutateAsync(m.id)
                                  } catch (err) {
                                    toast.error(err instanceof Error ? err.message : 'Failed to resend')
                                  }
                                }}
                                className="text-xs text-blue-600 hover:text-blue-700 hover:underline cursor-pointer transition-colors duration-150"
                              >
                                Resend
                              </button>
                              <span className="text-zinc-300">·</span>
                              <button
                                onClick={() =>
                                  setConfirmAction({
                                    title: 'Revoke invite',
                                    message: `Revoke the invite for ${m.email}? This cannot be undone.`,
                                    confirmLabel: 'Revoke',
                                    pendingLabel: 'Revoking…',
                                    onConfirm: async () => {
                                      await revokeMutation.mutateAsync(m.id)
                                      setConfirmAction(null)
                                      toast.success(`Invite for ${m.email} revoked`)
                                    },
                                  })
                                }
                                className="text-xs text-red-600 hover:text-red-700 hover:underline cursor-pointer transition-colors duration-150"
                              >
                                Revoke
                              </button>
                            </div>
                          )}

                          {/* ATS rows — single Send-invite action */}
                          {category === 'ats' && (
                            <button
                              disabled={pendingInviteId === m.id}
                              onClick={async () => {
                                setPendingInviteId(m.id)
                                try {
                                  await inviteFromAtsMutation.mutateAsync(m.email)
                                  toast.success(`Invite sent to ${m.email}`)
                                } catch (err) {
                                  toast.error(
                                    err instanceof Error ? err.message : 'Failed to send invite',
                                  )
                                } finally {
                                  setPendingInviteId(null)
                                }
                              }}
                              className="text-xs text-blue-600 hover:text-blue-700 hover:underline cursor-pointer transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {pendingInviteId === m.id ? 'Sending…' : 'Send invite'}
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      </div>
    </>
  );
}
