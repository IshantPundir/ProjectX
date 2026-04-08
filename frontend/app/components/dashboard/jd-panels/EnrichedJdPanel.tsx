'use client'

type Props = {
  enrichedJd: string
}

export function EnrichedJdPanel({ enrichedJd }: Props) {
  return (
    <section className="col-span-1 3xl:col-span-2 bg-white rounded-lg border border-zinc-200 p-6 overflow-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-4 pb-2 border-b border-zinc-100">
        Enriched JD
      </h3>
      <div className="text-sm text-zinc-700 whitespace-pre-wrap leading-relaxed">
        {enrichedJd}
      </div>
    </section>
  )
}
