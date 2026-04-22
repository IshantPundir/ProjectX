'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useEffect } from 'react'
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
import { ApiError } from '@/lib/api/client'
import type {
  CandidateCreate,
  CandidateResponse,
} from '@/lib/api/candidates'
import { useCreateCandidate } from '@/lib/hooks/use-create-candidate'

// Matches backend CandidateCreate (`extra="forbid"`): only the listed fields.
// `source`/`external_id`/`source_metadata` default server-side for manual
// entry, so the dialog doesn't send them.
const addCandidateSchema = z.object({
  name: z.string().trim().min(1, 'Name is required'),
  email: z
    .string()
    .trim()
    .min(1, 'Email is required')
    .email('Enter a valid email'),
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

type AddCandidateForm = z.infer<typeof addCandidateSchema>

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (candidate: CandidateResponse) => void
}

const DEFAULT_VALUES: AddCandidateForm = {
  name: '',
  email: '',
  phone: '',
  location: '',
  current_title: '',
  linkedin_url: '',
  notes: '',
}

export default function AddCandidateDialog({
  open,
  onOpenChange,
  onCreated,
}: Props) {
  const createMutation = useCreateCandidate()

  const form = useForm<AddCandidateForm>({
    resolver: zodResolver(addCandidateSchema),
    defaultValues: DEFAULT_VALUES,
    mode: 'onBlur',
  })

  // Reset the form whenever the dialog reopens so stale input from a prior
  // create doesn't linger. Separated from close-side reset to keep logic local.
  useEffect(() => {
    if (open) form.reset(DEFAULT_VALUES)
  }, [open, form])

  const handleClose = (next: boolean) => {
    if (!next && createMutation.isPending) return // don't close mid-submit
    onOpenChange(next)
  }

  const onSubmit = form.handleSubmit((values) => {
    // Backend model is `extra="forbid"`, so convert empty strings to
    // null/undefined rather than sending whitespace placeholders.
    const body: CandidateCreate = {
      name: values.name.trim(),
      email: values.email.trim(),
      phone: values.phone?.trim() || null,
      location: values.location?.trim() || null,
      current_title: values.current_title?.trim() || null,
      linkedin_url: values.linkedin_url?.trim() || null,
      notes: values.notes?.trim() || null,
    }

    createMutation.mutate(body, {
      onSuccess: (created) => {
        toast.success('Candidate created')
        onCreated?.(created)
        onOpenChange(false)
      },
      onError: (err) => {
        // apiFetch throws ApiError with an HTTP `status` field. 409 on
        // this endpoint is DUPLICATE_EMAIL (see candidates/errors.py) —
        // surface it on the email field and keep focus there.
        if (err instanceof ApiError && err.status === 409) {
          form.setError('email', {
            type: 'server',
            message: 'A candidate with this email already exists.',
          })
          form.setFocus('email')
          return
        }
        toast.error(err.message)
      },
    })
  })

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add Candidate</DialogTitle>
          <DialogDescription>
            Create a candidate profile. You can upload their resume and
            assign them to a job from the candidate detail page.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="candidate-name" className="text-sm font-semibold">
                Name <span className="text-red-500">*</span>
              </Label>
              <Input
                id="candidate-name"
                {...form.register('name')}
                className="mt-2"
                autoComplete="off"
                aria-invalid={!!form.formState.errors.name || undefined}
              />
              {form.formState.errors.name && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.name.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="candidate-email"
                className="text-sm font-semibold"
              >
                Email <span className="text-red-500">*</span>
              </Label>
              <Input
                id="candidate-email"
                type="email"
                {...form.register('email')}
                className="mt-2"
                autoComplete="off"
                aria-invalid={!!form.formState.errors.email || undefined}
              />
              {form.formState.errors.email && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.email.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="candidate-phone"
                className="text-sm font-semibold"
              >
                Phone
              </Label>
              <Input
                id="candidate-phone"
                type="tel"
                {...form.register('phone')}
                className="mt-2"
                autoComplete="off"
                aria-invalid={!!form.formState.errors.phone || undefined}
              />
              {form.formState.errors.phone && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.phone.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="candidate-location"
                className="text-sm font-semibold"
              >
                Location
              </Label>
              <Input
                id="candidate-location"
                {...form.register('location')}
                className="mt-2"
                autoComplete="off"
                aria-invalid={!!form.formState.errors.location || undefined}
              />
              {form.formState.errors.location && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.location.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="candidate-title"
                className="text-sm font-semibold"
              >
                Current title
              </Label>
              <Input
                id="candidate-title"
                {...form.register('current_title')}
                className="mt-2"
                autoComplete="off"
                aria-invalid={
                  !!form.formState.errors.current_title || undefined
                }
              />
              {form.formState.errors.current_title && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.current_title.message}
                </p>
              )}
            </div>

            <div>
              <Label
                htmlFor="candidate-linkedin"
                className="text-sm font-semibold"
              >
                LinkedIn URL
              </Label>
              <Input
                id="candidate-linkedin"
                type="url"
                placeholder="https://linkedin.com/in/…"
                {...form.register('linkedin_url')}
                className="mt-2"
                autoComplete="off"
                aria-invalid={
                  !!form.formState.errors.linkedin_url || undefined
                }
              />
              {form.formState.errors.linkedin_url && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.linkedin_url.message}
                </p>
              )}
            </div>
          </div>

          <div>
            <Label htmlFor="candidate-notes" className="text-sm font-semibold">
              Notes
            </Label>
            <Textarea
              id="candidate-notes"
              rows={4}
              {...form.register('notes')}
              className="mt-2"
              aria-invalid={!!form.formState.errors.notes || undefined}
            />
            {form.formState.errors.notes && (
              <p className="text-xs text-red-500 mt-1">
                {form.formState.errors.notes.message}
              </p>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleClose(false)}
              disabled={createMutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
