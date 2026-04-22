'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/px'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/px'
import { Input } from '@/components/px'
import { Label } from '@/components/px'
import { Textarea } from '@/components/px'
import {
  candidatesApi,
  type CandidateResponse,
  type CandidateUpdate,
} from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import ResumeUploadField from '../ResumeUploadField'

// Mirrors the fields in CandidateUpdate that the user can edit inline. `email`
// is intentionally omitted — changing identity email is a different workflow
// (and the backend update endpoint does not allow it).
const profileSchema = z.object({
  name: z.string().trim().min(1, 'Name is required'),
  phone: z
    .string()
    .trim()
    .max(50, 'Phone must be 50 characters or fewer')
    .optional()
    .or(z.literal('')),
  location: z
    .string()
    .trim()
    .max(200, 'Location must be 200 characters or fewer')
    .optional()
    .or(z.literal('')),
  current_title: z
    .string()
    .trim()
    .max(200, 'Title must be 200 characters or fewer')
    .optional()
    .or(z.literal('')),
  linkedin_url: z
    .string()
    .trim()
    .url('Enter a valid URL (including https://)')
    .optional()
    .or(z.literal('')),
  notes: z
    .string()
    .max(5000, 'Notes must be 5000 characters or fewer')
    .optional()
    .or(z.literal('')),
})

type ProfileForm = z.infer<typeof profileSchema>

function formDefaults(candidate: CandidateResponse): ProfileForm {
  return {
    name: candidate.name ?? '',
    phone: candidate.phone ?? '',
    location: candidate.location ?? '',
    current_title: candidate.current_title ?? '',
    linkedin_url: candidate.linkedin_url ?? '',
    notes: candidate.notes ?? '',
  }
}

interface Props {
  candidate: CandidateResponse
}

export default function CandidateProfileTab({ candidate }: Props) {
  const queryClient = useQueryClient()
  const [redactOpen, setRedactOpen] = useState(false)

  const form = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    defaultValues: formDefaults(candidate),
    mode: 'onBlur',
  })

  // If the candidate prop updates (e.g. after a successful save triggers a
  // refetch of `['candidates', id]`), reset the form so the baseline matches
  // the fresh server state. Without this, `isDirty` stays true after save.
  useEffect(() => {
    form.reset(formDefaults(candidate))
  }, [candidate, form])

  const updateMutation = useMutation<
    CandidateResponse,
    Error,
    CandidateUpdate
  >({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return candidatesApi.update(token, candidate.id, body)
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(['candidates', candidate.id], updated)
      void queryClient.invalidateQueries({ queryKey: ['candidates-list'] })
      toast.success('Changes saved')
    },
    onError: (err) => {
      toast.error(err.message)
    },
  })

  const redactMutation = useMutation<void, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      await candidatesApi.redactPii(token, candidate.id)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidate.id],
      })
      void queryClient.invalidateQueries({ queryKey: ['candidates-list'] })
      toast.success('PII redacted')
      setRedactOpen(false)
    },
    onError: (err) => {
      // The backend returns 403 when the caller isn't a super admin. We
      // intentionally don't gate the button on the client (no super-admin
      // hook) — surface the error directly instead.
      toast.error(err.message)
    },
  })

  const isRedacted = candidate.pii_redacted_at !== null

  const onSubmit = form.handleSubmit((values) => {
    const body: CandidateUpdate = {
      name: values.name.trim(),
      phone: values.phone?.trim() || null,
      location: values.location?.trim() || null,
      current_title: values.current_title?.trim() || null,
      linkedin_url: values.linkedin_url?.trim() || null,
      notes: values.notes?.trim() || null,
    }
    updateMutation.mutate(body)
  })

  return (
    <div className="space-y-6">
      <section
        className="rounded-[10px] border p-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h2
          className="mb-4 text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Profile
        </h2>

        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label
                htmlFor="profile-name"
                className="text-sm font-semibold"
              >
                Name <span style={{ color: 'var(--px-danger)' }}>*</span>
              </Label>
              <Input
                id="profile-name"
                {...form.register('name')}
                className="mt-2"
                disabled={isRedacted}
                aria-invalid={!!form.formState.errors.name || undefined}
              />
              {form.formState.errors.name && (
                <p className="mt-1 text-[11.5px]" style={{ color: 'var(--px-danger)' }}>
                  {form.formState.errors.name.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="profile-email"
                className="text-sm font-semibold"
              >
                Email
              </Label>
              <Input
                id="profile-email"
                type="email"
                value={candidate.email ?? ''}
                disabled
                readOnly
                className="mt-2"
              />
              <p className="mt-1 text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
                Email is the candidate&apos;s identity key and cannot be
                edited here.
              </p>
            </div>

            <div>
              <Label
                htmlFor="profile-phone"
                className="text-sm font-semibold"
              >
                Phone
              </Label>
              <Input
                id="profile-phone"
                type="tel"
                {...form.register('phone')}
                className="mt-2"
                disabled={isRedacted}
                aria-invalid={!!form.formState.errors.phone || undefined}
              />
              {form.formState.errors.phone && (
                <p className="mt-1 text-[11.5px]" style={{ color: 'var(--px-danger)' }}>
                  {form.formState.errors.phone.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="profile-location"
                className="text-sm font-semibold"
              >
                Location
              </Label>
              <Input
                id="profile-location"
                {...form.register('location')}
                className="mt-2"
                disabled={isRedacted}
                aria-invalid={!!form.formState.errors.location || undefined}
              />
              {form.formState.errors.location && (
                <p className="mt-1 text-[11.5px]" style={{ color: 'var(--px-danger)' }}>
                  {form.formState.errors.location.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="profile-title"
                className="text-sm font-semibold"
              >
                Current title
              </Label>
              <Input
                id="profile-title"
                {...form.register('current_title')}
                className="mt-2"
                disabled={isRedacted}
                aria-invalid={
                  !!form.formState.errors.current_title || undefined
                }
              />
              {form.formState.errors.current_title && (
                <p className="mt-1 text-[11.5px]" style={{ color: 'var(--px-danger)' }}>
                  {form.formState.errors.current_title.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="profile-linkedin"
                className="text-sm font-semibold"
              >
                LinkedIn URL
              </Label>
              <Input
                id="profile-linkedin"
                type="url"
                placeholder="https://linkedin.com/in/…"
                {...form.register('linkedin_url')}
                className="mt-2"
                disabled={isRedacted}
                aria-invalid={
                  !!form.formState.errors.linkedin_url || undefined
                }
              />
              {form.formState.errors.linkedin_url && (
                <p className="mt-1 text-[11.5px]" style={{ color: 'var(--px-danger)' }}>
                  {form.formState.errors.linkedin_url.message}
                </p>
              )}
            </div>
          </div>

          <div>
            <Label
              htmlFor="profile-notes"
              className="text-sm font-semibold"
            >
              Notes
            </Label>
            <Textarea
              id="profile-notes"
              rows={4}
              {...form.register('notes')}
              className="mt-2"
              disabled={isRedacted}
              aria-invalid={!!form.formState.errors.notes || undefined}
            />
            {form.formState.errors.notes && (
              <p className="text-xs text-red-500 mt-1">
                {form.formState.errors.notes.message}
              </p>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => form.reset(formDefaults(candidate))}
              disabled={
                !form.formState.isDirty ||
                updateMutation.isPending ||
                isRedacted
              }
            >
              Reset
            </Button>
            <Button
              type="submit"
              disabled={
                !form.formState.isDirty ||
                updateMutation.isPending ||
                isRedacted
              }
            >
              {updateMutation.isPending ? 'Saving…' : 'Save Changes'}
            </Button>
          </div>
        </form>
      </section>

      <section
        className="rounded-[10px] border p-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h2
          className="mb-4 text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Resume
        </h2>
        {isRedacted ? (
          <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
            Resume unavailable — candidate PII has been redacted.
          </p>
        ) : (
          <ResumeUploadField
            candidateId={candidate.id}
            currentResumeKey={candidate.resume_s3_key}
          />
        )}
      </section>

      {!isRedacted && (
        <section
          className="rounded-[10px] border p-6"
          style={{
            background: 'var(--px-danger-bg)',
            borderColor: 'var(--px-danger-line)',
          }}
        >
          <h2
            className="mb-1 text-[11px] font-semibold uppercase"
            style={{ letterSpacing: '1.1px', color: 'var(--px-danger)' }}
          >
            Danger zone
          </h2>
          <p className="mb-4 text-sm" style={{ color: 'var(--px-fg-2)' }}>
            Permanently remove this candidate&apos;s personal information.
            Only super admins can perform this action. Audit trail is
            preserved.
          </p>
          <Button
            type="button"
            variant="destructive"
            onClick={() => setRedactOpen(true)}
            disabled={redactMutation.isPending}
          >
            Redact PII
          </Button>
        </section>
      )}

      <Dialog
        open={redactOpen}
        onOpenChange={(next) => {
          if (!next && redactMutation.isPending) return
          setRedactOpen(next)
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Redact candidate PII?</DialogTitle>
            <DialogDescription>
              This permanently removes the candidate&apos;s name, email,
              phone, location, title, LinkedIn URL, notes, and resume.
              Audit records are preserved. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setRedactOpen(false)}
              disabled={redactMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => redactMutation.mutate()}
              disabled={redactMutation.isPending}
            >
              {redactMutation.isPending ? 'Redacting…' : 'Redact PII'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
