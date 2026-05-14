'use client'

import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

import {
  CompanyProfileForm,
  type CompanyProfile,
} from '@/components/dashboard/company-profile-form'
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
 *   stage 'profile' — only for `client_account`: opens a CompanyProfileForm
 *                     because the backend rejects null `company_profile`
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

  const form = useForm<NameValues>({
    resolver: zodResolver(nameSchema),
    defaultValues: { name: '' },
  })

  // Reset when the target changes (a different parent / child type).
  useEffect(() => {
    if (open) {
      setStage('name')
      setSubmitError(null)
      form.reset({ name: '' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.parentId, target?.childType])

  async function doCreate(profile: CompanyProfile | null) {
    if (!target) return
    const name = form.getValues('name').trim()
    try {
      const created = await createMutation.mutateAsync({
        name,
        unit_type: target.childType,
        parent_unit_id: target.parentId,
        // Map old CompanyProfile shape to column-level fields.
        // Task 10 will replace the profile dialog with inline editing.
        about: profile?.about ?? null,
        industry: profile?.industry ?? null,
        hiring_bar: profile?.hiring_bar ?? null,
        metadata: null,
      })
      onCreated(created.id)
    } catch (err) {
      // Field-level errors (422) attach to the form; others surface
      // inline so the user can recover without closing the dialog.
      if (applyApiErrorToForm(err, form)) return
      setSubmitError(
        err instanceof Error ? err.message : 'Failed to create unit',
      )
    }
  }

  const onNameSubmit = form.handleSubmit(async () => {
    setSubmitError(null)
    if (target?.childType === 'client_account') {
      // Cascade to the profile dialog — backend requires it.
      setStage('profile')
      return
    }
    await doCreate(null)
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
                {...form.register('name')}
              />
              {form.formState.errors.name && (
                <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                  {form.formState.errors.name.message}
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
                {form.getValues('name').trim()}
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
          <CompanyProfileForm
            onSubmit={async (profile) => {
              setSubmitError(null)
              await doCreate(profile)
            }}
            submitLabel="Create client account"
          />
        </DialogContent>
      </Dialog>
    </>
  )
}
