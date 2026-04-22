'use client'

import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/px'
import { useCreateQuestion } from '@/lib/hooks/use-save-question'

const schema = z.object({
  text: z.string().min(10).max(500),
  signal_values: z.array(z.string()).min(1).max(3),
  estimated_minutes: z.number().gt(0).lte(15),
  is_mandatory: z.boolean(),
  follow_ups: z.array(z.string()).max(3),
  positive_evidence: z.array(z.string()).max(5),
  red_flags: z.array(z.string()).max(3),
  rubric: z.object({
    excellent: z.string().min(20).max(300),
    meets_bar: z.string().min(20).max(300),
    below_bar: z.string().min(20).max(300),
  }),
  evaluation_hint: z.string().min(10).max(200),
})

type Form = z.infer<typeof schema>

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
  onClose: () => void
}

export function AddCustomQuestionDialog({ jobId, stageId, onClose }: Props) {
  const createMutation = useCreateQuestion(jobId, stageId)
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: {
      text: '',
      signal_values: [],
      estimated_minutes: 5,
      is_mandatory: false,
      follow_ups: [],
      positive_evidence: ['', '', ''],
      red_flags: ['', ''],
      rubric: { excellent: '', meets_bar: '', below_bar: '' },
      evaluation_hint: '',
    },
  })

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  function onSubmit(data: Form) {
    createMutation.mutate(data, {
      onSuccess: () => onClose(),
    })
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-question-heading"
        className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h3 id="add-question-heading" className="text-base font-semibold">
            Add custom question
          </h3>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="p-5 space-y-4 overflow-y-auto"
        >
          {/* Basic fields */}
          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">
              Question text
            </label>
            <textarea
              {...form.register('text')}
              rows={3}
              className="w-full text-sm border border-zinc-200 rounded px-3 py-2"
            />
            {form.formState.errors.text && (
              <p className="text-xs text-red-600 mt-1">
                {form.formState.errors.text.message}
              </p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">
              Signal values (1–3)
            </label>
            <div className="text-[11px] text-zinc-500 mb-2">
              Comma-separated list of signals from the job&apos;s pinned
              snapshot. Must match signals whose type matches this stage&apos;s
              signal filter.
            </div>
            <input
              type="text"
              placeholder="e.g., Kubernetes, Incident response"
              onChange={(e) =>
                form.setValue(
                  'signal_values',
                  e.target.value
                    .split(',')
                    .map((s) => s.trim())
                    .filter(Boolean),
                  { shouldValidate: true },
                )
              }
              className="w-full text-sm border border-zinc-200 rounded px-3 py-2"
            />
            {form.formState.errors.signal_values && (
              <p className="text-xs text-red-600 mt-1">
                {form.formState.errors.signal_values.message ||
                  'Signal values must be provided (1-3)'}
              </p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">
              Estimated minutes
            </label>
            <input
              type="number"
              min="1"
              max="15"
              step="0.5"
              {...form.register('estimated_minutes', { valueAsNumber: true })}
              className="w-full text-sm border border-zinc-200 rounded px-3 py-2"
            />
          </div>

          <div>
            <label className="inline-flex items-center gap-2 text-xs">
              <input type="checkbox" {...form.register('is_mandatory')} />
              Mandatory (must be asked during the interview)
            </label>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-700 mb-1">
              Evaluation hint
            </label>
            <textarea
              {...form.register('evaluation_hint')}
              rows={2}
              className="w-full text-xs border border-zinc-200 rounded px-3 py-2"
              placeholder="1–2 sentences summarizing what a strong answer contains"
            />
          </div>

          <div className="space-y-2">
            <div className="text-xs font-medium text-zinc-700">
              Rubric anchors
            </div>
            {(['excellent', 'meets_bar', 'below_bar'] as const).map((level) => (
              <div key={level}>
                <label className="block text-[10px] uppercase text-zinc-500 mb-1">
                  {level}
                </label>
                <textarea
                  {...form.register(`rubric.${level}` as const)}
                  rows={2}
                  className="w-full text-xs border border-zinc-200 rounded px-3 py-2"
                />
              </div>
            ))}
          </div>

          <div className="flex justify-end gap-2 pt-3 border-t border-zinc-100">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating…' : 'Create question'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
