'use client'

import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { candidatesApi } from '@/lib/api/candidates'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { useResumeUpload } from '@/lib/hooks/use-resume-upload'

const MAX_RESUME_BYTES = 10 * 1024 * 1024 // 10 MB

interface Props {
  candidateId: string
  currentResumeKey: string | null
  onChange?: (uploaded: boolean) => void
}

export default function ResumeUploadField({
  candidateId,
  currentResumeKey,
  onChange,
}: Props) {
  const uploadMutation = useResumeUpload(candidateId)
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement | null>(null)
  // Local view of the upload state — lets us flip immediately on success/
  // removal without waiting for the `use-candidate` query to refetch. When
  // parent re-renders with a fresh `currentResumeKey`, the derived UI matches.
  const [justUploaded, setJustUploaded] = useState(false)

  const deleteMutation = useMutation<void, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      await candidatesApi.deleteResume(token, candidateId)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['candidates', candidateId],
      })
    },
  })

  const hasResume = !!currentResumeKey || justUploaded

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset the input so the same file can be re-selected after an error.
    e.target.value = ''
    if (!file) return

    if (file.type !== 'application/pdf') {
      toast.error('Resume must be a PDF file')
      return
    }
    if (file.size > MAX_RESUME_BYTES) {
      toast.error('Resume must be 10 MB or smaller')
      return
    }

    uploadMutation.mutate(file, {
      onSuccess: () => {
        setJustUploaded(true)
        toast.success('Resume uploaded')
        onChange?.(true)
      },
      onError: (err) => {
        toast.error(`Upload failed: ${err.message}`)
      },
    })
  }

  const handleRemove = () => {
    deleteMutation.mutate(undefined, {
      onSuccess: () => {
        setJustUploaded(false)
        toast.success('Resume removed')
        onChange?.(false)
      },
      onError: (err) => {
        toast.error(`Remove failed: ${err.message}`)
      },
    })
  }

  const isUploading = uploadMutation.isPending
  const isRemoving = deleteMutation.isPending

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={handleFileChange}
        aria-label="Resume PDF file"
      />

      {hasResume ? (
        <div className="flex items-center gap-3">
          <span
            className="inline-flex items-center gap-1.5 text-sm text-green-700"
            aria-live="polite"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              fill="currentColor"
              className="size-4"
            >
              <path
                fillRule="evenodd"
                d="M16.704 5.29a1 1 0 0 1 0 1.42l-8 8a1 1 0 0 1-1.42 0l-4-4a1 1 0 0 1 1.42-1.42L8 12.58l7.29-7.29a1 1 0 0 1 1.414 0Z"
                clipRule="evenodd"
              />
            </svg>
            Resume uploaded
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => inputRef.current?.click()}
            disabled={isUploading || isRemoving}
          >
            Replace
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={handleRemove}
            disabled={isUploading || isRemoving}
          >
            {isRemoving ? 'Removing…' : 'Remove'}
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => inputRef.current?.click()}
            disabled={isUploading}
          >
            {isUploading ? 'Uploading…' : 'Upload resume (PDF)'}
          </Button>
          {isUploading && (
            <span
              className="text-xs text-zinc-500"
              aria-live="polite"
            >
              Uploading…
            </span>
          )}
        </div>
      )}

      <p className="text-xs text-zinc-500">PDF only, 10 MB max.</p>
    </div>
  )
}
