'use client'

import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/px'
import { applyApiErrorToForm } from '@/lib/api/errors'
import { useCreateOrgUnit } from '@/lib/hooks/use-create-org-unit'

import type { UnitType } from './unit-type-style'

const TYPE_LABEL: Record<UnitType, string> = {
  company: 'company',
  client_account: 'client account',
  region: 'region',
  division: 'division',
  team: 'team',
}

const PLACEHOLDER: Record<UnitType, string> = {
  company: 'e.g., Acme Inc.',
  client_account: 'e.g., Acme Inc.',
  region: 'e.g., North America',
  division: 'e.g., Engineering',
  team: 'e.g., Frontend',
}

const nameSchema = z.object({
  name: z
    .string()
    .min(1, 'Name is required')
    .max(120, 'Name is too long'),
})
type NameValues = z.infer<typeof nameSchema>

// Inline profile fields for client_account creation.
// company_stage is dropped (refactor T4); industry is free-text.
//
// All free-text, no length caps — matches the detail-page schema in
// `app/(dashboard)/settings/org-units/[unitId]/schema.ts` and the
// backend's `CompanyContext` wire contract. The `/ 500` and `/ 280`
// indicators below the textareas are soft target hints, not enforced
// limits: longer values flow through to the LLM prompt verbatim.
// Non-emptiness on `about` is the only hard requirement here because
// downstream activation gates (find_company_profile_in_ancestry) refuse
// the unit as a profile owner if any of about/industry/hiring_bar is
// empty after strip.
const profileSchema = z.object({
  about: z.string().min(1, 'Describe what you build'),
  industry: z.string().optional(),
  hiring_bar: z.string().optional(),
})
type ProfileValues = z.infer<typeof profileSchema>

export interface CreateChildTarget {
  parentId: string
  parentName: string
  childType: UnitType
}

interface Props {
  /** When non-null the dialog is open. Setting it to null closes. */
  target: CreateChildTarget | null
  onClose: () => void
  /** Fired with the new unit's id once the create mutation resolves. */
  onCreated: (newUnitId: string) => void
}

/**
 * Two-stage create flow used by the org-graph spider menu.
 *
 *   stage 'name'    — single text input ("Create new {type} under {parent}")
 *   stage 'profile' — only for `client_account`: inline about/industry/hiring_bar
 *                     fields because the backend requires a non-null company profile
 *                     for client accounts.
 *
 * The parent unit is wired automatically via `target.parentId`. On
 * success the parent navigates the user to the new unit's detail page.
 */
export function OrgUnitCreateDialog({ target, onClose, onCreated }: Props) {
  const open = target !== null
  const [stage, setStage] = useState<'name' | 'profile'>('name')
  const [submitError, setSubmitError] = useState<string | null>(null)
  const createMutation = useCreateOrgUnit()

  const nameForm = useForm<NameValues>({
    resolver: zodResolver(nameSchema),
    defaultValues: { name: '' },
  })

  const profileForm = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: { about: '', industry: '', hiring_bar: '' },
    mode: 'onChange',
  })

  const aboutValue = profileForm.watch('about') || ''
  const hiringBarValue = profileForm.watch('hiring_bar') || ''

  // Reset when the target changes (a different parent / child type).
  useEffect(() => {
    if (open) {
      setStage('name')
      setSubmitError(null)
      nameForm.reset({ name: '' })
      profileForm.reset({ about: '', industry: '', hiring_bar: '' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.parentId, target?.childType])

  async function doCreate(profile: ProfileValues | null) {
    if (!target) return
    const name = nameForm.getValues('name').trim()
    try {
      const created = await createMutation.mutateAsync({
        name,
        unit_type: target.childType,
        parent_unit_id: target.parentId,
        about: profile?.about ?? null,
        industry: profile?.industry ?? null,
        hiring_bar: profile?.hiring_bar ?? null,
        metadata: null,
      })
      onCreated(created.id)
    } catch (err) {
      // Field-level errors (422) attach to the form; others surface
      // inline so the user can recover without closing the dialog.
      if (applyApiErrorToForm(err, nameForm)) return
      setSubmitError(
        err instanceof Error ? err.message : 'Failed to create unit',
      )
    }
  }

  const onNameSubmit = nameForm.handleSubmit(async () => {
    setSubmitError(null)
    if (target?.childType === 'client_account') {
      // Cascade to the profile dialog — backend requires it.
      setStage('profile')
      return
    }
    await doCreate(null)
  })

  const onProfileSubmit = profileForm.handleSubmit(async (values) => {
    setSubmitError(null)
    await doCreate(values)
  })

  function handleOpenChange(next: boolean) {
    if (next) return
    if (createMutation.isPending) return
    onClose()
  }

  if (!target) {
    // Render nothing while closed so we don't ship a hidden dialog
    // with stale form state.
    return null
  }

  const typeLabel = TYPE_LABEL[target.childType]

  return (
    <>
      <Dialog open={open && stage === 'name'} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create new {typeLabel}</DialogTitle>
            <DialogDescription>
              Under{' '}
              <span
                className="font-medium"
                style={{ color: 'var(--px-fg)' }}
              >
                {target.parentName}
              </span>
              .
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={onNameSubmit} className="space-y-4">
            <div>
              <label htmlFor="create-child-name" className="px-label">
                Name
              </label>
              <input
                id="create-child-name"
                type="text"
                autoFocus
                className="px-input"
                placeholder={PLACEHOLDER[target.childType]}
                {...nameForm.register('name')}
              />
              {nameForm.formState.errors.name && (
                <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                  {nameForm.formState.errors.name.message}
                </p>
              )}
            </div>
            {submitError && (
              <p
                role="alert"
                className="text-[12px]"
                style={{ color: 'var(--px-danger)' }}
              >
                {submitError}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                disabled={createMutation.isPending}
                className="px-btn ghost sm"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={createMutation.isPending}
                className="px-btn primary sm"
              >
                {createMutation.isPending
                  ? 'Creating…'
                  : target.childType === 'client_account'
                    ? 'Continue'
                    : `Create ${typeLabel}`}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={open && stage === 'profile'}
        onOpenChange={handleOpenChange}
      >
        <DialogContent widthClass="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Client account profile</DialogTitle>
            <DialogDescription>
              Set up the profile for{' '}
              <span
                className="font-medium"
                style={{ color: 'var(--px-fg)' }}
              >
                {nameForm.getValues('name').trim()}
              </span>
              . This describes the end client — the company your recruiters
              are hiring <em>for</em>. It feeds the AI when generating JD
              enhancements and interview questions for this client&apos;s
              roles.
            </DialogDescription>
          </DialogHeader>
          {submitError && (
            <p
              role="alert"
              className="text-[12px]"
              style={{ color: 'var(--px-danger)' }}
            >
              {submitError}
            </p>
          )}
          <form onSubmit={onProfileSubmit} className="space-y-6 max-w-2xl">
            <div>
              <div className="flex items-baseline justify-between">
                <label htmlFor="ca-about" className="px-label text-sm font-semibold">
                  What does this client actually build or do?
                </label>
                <span className="text-xs text-zinc-400">{aboutValue.length} / 500</span>
              </div>
              <p className="text-xs text-zinc-500 mt-1 mb-2">
                Be specific — what problems, at what scale, for whom?{' '}
                <em>Not their mission statement.</em>
              </p>
              <textarea
                id="ca-about"
                className="px-input"
                rows={4}
                {...profileForm.register('about')}
              />
              {profileForm.formState.errors.about && (
                <p className="text-xs mt-1" style={{ color: 'var(--px-danger)' }}>
                  {profileForm.formState.errors.about.message}
                </p>
              )}
            </div>

            <div>
              <label htmlFor="ca-industry" className="px-label text-sm font-semibold">
                Industry <span className="font-normal text-zinc-400">(optional)</span>
              </label>
              <input
                id="ca-industry"
                type="text"
                className="px-input"
                placeholder="e.g., Fintech / Financial Services"
                {...profileForm.register('industry')}
              />
              {profileForm.formState.errors.industry && (
                <p className="text-xs mt-1" style={{ color: 'var(--px-danger)' }}>
                  {profileForm.formState.errors.industry.message}
                </p>
              )}
            </div>

            <div>
              <div className="flex items-baseline justify-between">
                <label htmlFor="ca-hiring-bar" className="px-label text-sm font-semibold">
                  What does a strong hire look like here?{' '}
                  <span className="font-normal text-zinc-400">(optional)</span>
                </label>
                <span className="text-xs text-zinc-400">{hiringBarValue.length} / 280</span>
              </div>
              <p className="text-xs text-zinc-500 mt-1 mb-2">
                What does this client value that a generic JD wouldn&apos;t capture?
              </p>
              <textarea
                id="ca-hiring-bar"
                className="px-input"
                rows={3}
                {...profileForm.register('hiring_bar')}
              />
              {profileForm.formState.errors.hiring_bar && (
                <p className="text-xs mt-1" style={{ color: 'var(--px-danger)' }}>
                  {profileForm.formState.errors.hiring_bar.message}
                </p>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setStage('name')}
                disabled={createMutation.isPending}
                className="px-btn ghost sm"
              >
                Back
              </button>
              <button
                type="submit"
                disabled={!profileForm.formState.isValid || createMutation.isPending}
                className="px-btn primary sm"
              >
                {createMutation.isPending ? 'Creating…' : 'Create client account'}
              </button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
