import type { ScoringManifest } from '@/lib/api/reports'

export function ReportMethodologyFooter({ manifest }: { manifest: ScoringManifest | null }) {
  const items: string[] = []
  if (manifest?.scorer_model) items.push(`scorer ${manifest.scorer_model}${manifest.reasoning_effort ? ` · ${manifest.reasoning_effort}` : ''}`)
  if (manifest?.prompt_version) items.push(`prompt ${manifest.prompt_version}`)
  items.push('verbal-content-only')
  if (manifest?.generated_at) items.push(`generated ${new Date(manifest.generated_at).toLocaleDateString()}`)
  if (manifest?.correlation_id) items.push(`corr ${manifest.correlation_id.slice(0, 8)}`)

  return (
    <footer className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t px-1 pt-3 text-[9.5px]" style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-4)' }}>
      <span className="font-bold" style={{ color: 'var(--px-fg-3)' }}>Methodology</span>
      {items.map((it, i) => <span key={i}>{it}</span>)}
    </footer>
  )
}
