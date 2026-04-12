'use client'

import Link from 'next/link'
import type { PipelineTemplate } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

type Props = {
  template: PipelineTemplate
  editHref: string
  onSetDefault?: () => void
  onDelete?: () => void
  canManage?: boolean
}

export function TemplateLibraryCard({ template, editHref, onSetDefault, onDelete, canManage = true }: Props) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg p-5 hover:border-zinc-300 transition">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-zinc-900">{template.name}</h3>
          {template.is_default && <Badge variant="default">Default</Badge>}
          {template.from_starter && <Badge variant="secondary">Starter</Badge>}
        </div>
      </div>
      {template.description && (
        <p className="text-xs text-zinc-500 mb-3">{template.description}</p>
      )}
      <div className="text-xs text-zinc-600 mb-4">
        {template.stages.length} stage{template.stages.length !== 1 ? 's' : ''} · {template.stages.map((s) => s.name).join(' → ')}
      </div>
      <div className="flex gap-2">
        <Link href={editHref}>
          <Button size="sm" variant="outline">
            Edit
          </Button>
        </Link>
        {canManage && !template.is_default && onSetDefault && (
          <Button size="sm" variant="outline" onClick={onSetDefault}>
            Set as default
          </Button>
        )}
        {canManage && !template.is_default && onDelete && (
          <Button size="sm" variant="destructive" onClick={onDelete}>
            Delete
          </Button>
        )}
      </div>
    </div>
  )
}
