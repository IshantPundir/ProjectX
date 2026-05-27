import type { MethodologyOut, ScoringManifest } from '@/lib/api/reports'

export function ReportMethodologyFooter({ methodology, manifest }: { methodology: MethodologyOut; manifest: ScoringManifest | null }) {
  const meta: string[] = []
  if (manifest?.scorer_model) meta.push(`scorer ${manifest.scorer_model}${manifest.reasoning_effort ? ` · ${manifest.reasoning_effort}` : ''}`)
  if (manifest?.prompt_version) meta.push(`prompt ${manifest.prompt_version}`)
  meta.push('verbal-content-only')
  if (manifest?.generated_at) meta.push(`generated ${new Date(manifest.generated_at).toLocaleDateString()}`)
  if (manifest?.correlation_id) meta.push(`corr ${manifest.correlation_id.slice(0, 8)}`)

  return (
    <footer className="mt-4 border-t px-1 pt-3" style={{ borderColor: 'var(--px-hairline)' }}>
      <p className="text-[12px] leading-relaxed" style={{ color: 'var(--px-fg-3)' }}>
        <span className="font-bold">About this report. </span>{methodology.note}
      </p>
      {methodology.charity_flags.map((f, i) => (
        <p key={i} className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-4)' }}>⚑ {f}</p>
      ))}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px]" style={{ color: 'var(--px-fg-4)' }}>
        <span className="font-bold" style={{ color: 'var(--px-fg-3)' }}>Methodology</span>
        {meta.map((it, i) => <span key={i}>{it}</span>)}
      </div>
    </footer>
  )
}
