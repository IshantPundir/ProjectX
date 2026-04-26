'use client'

import type { PipelineCreateRequest } from '@/lib/api/pipelines'

export type RecentTemplate = {
  id: string
  name: string
  stage_count: number
  last_used: string
}

type StarterDef = {
  key: 'standard_technical' | 'fast_track' | 'screening_only' | 'senior_leadership'
  label: string
  subtitle: string
}

const STARTERS: StarterDef[] = [
  {
    key: 'standard_technical',
    label: 'Standard Technical',
    subtitle: 'Phone Screen → AI Screen → Human Interview',
  },
  {
    key: 'fast_track',
    label: 'Fast Track',
    subtitle: 'Phone Screen → AI Screen',
  },
  {
    key: 'screening_only',
    label: 'Screening Only',
    subtitle: 'Phone Screen only',
  },
  {
    key: 'senior_leadership',
    label: 'Senior Leadership',
    subtitle: 'Phone → AI Screen → Two Human Interviews',
  },
]

const BLANK_STAGES = [
  { position: 0, name: 'Intake', stage_type: 'intake' as const },
  { position: 1, name: 'Debrief', stage_type: 'debrief' as const },
]

export function PipelineSourcePicker({
  recentTemplates,
  teamDefault,
  onPick,
}: {
  /** Job ID — reserved for future use (e.g. deep-link back to job context). */
  jobId: string
  recentTemplates: RecentTemplate[]
  teamDefault: RecentTemplate | null
  onPick: (body: PipelineCreateRequest) => void
}) {
  const recentDeduped = teamDefault
    ? recentTemplates.filter((t) => t.id !== teamDefault.id)
    : recentTemplates
  const recentTop3 = recentDeduped.slice(0, 3)

  return (
    <div className="space-y-8 p-8">
      <h2 className="text-xl font-semibold text-zinc-900">
        Choose a starting point for this job&apos;s pipeline
      </h2>

      {recentTop3.length > 0 && (
        <Section title="Recent templates">
          {recentTop3.map((t) => (
            <TemplateCard
              key={t.id}
              template={t}
              onClick={() => onPick({ source: 'template', template_id: t.id })}
            />
          ))}
        </Section>
      )}

      {teamDefault && (
        <Section title="Team default">
          <TemplateCard
            template={teamDefault}
            starred
            onClick={() => onPick({ source: 'template', template_id: teamDefault.id })}
          />
        </Section>
      )}

      <Section title="System starters">
        {STARTERS.map((s) => (
          <button
            key={s.key}
            type="button"
            aria-label={s.label}
            onClick={() => onPick({ source: 'starter', starter_key: s.key })}
            className="bg-white rounded-lg border border-zinc-200 p-4 text-left hover:border-zinc-400 transition"
          >
            <div className="text-sm font-semibold text-zinc-900">{s.label}</div>
            <div className="text-xs text-zinc-500 mt-1">{s.subtitle}</div>
          </button>
        ))}
      </Section>

      <Section title="Or start blank">
        <button
          type="button"
          aria-label="Build from scratch"
          onClick={() => onPick({ source: 'scratch', stages: BLANK_STAGES })}
          className="bg-white rounded-lg border border-zinc-200 p-4 text-left hover:border-zinc-400 transition"
        >
          <div className="text-sm font-semibold text-zinc-900">Build from scratch</div>
          <div className="text-xs text-zinc-500 mt-1">
            Just intake + debrief; you add the middle stages.
          </div>
        </button>
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">{children}</div>
    </section>
  )
}

function TemplateCard({
  template,
  onClick,
  starred,
}: {
  template: RecentTemplate
  onClick: () => void
  starred?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={template.name}
      onClick={onClick}
      className="bg-white rounded-lg border border-zinc-200 p-4 text-left hover:border-zinc-400 transition"
    >
      <div className="text-sm font-semibold text-zinc-900">
        {starred ? '★ ' : ''}
        {template.name}
      </div>
      <div className="text-xs text-zinc-500 mt-1">
        {template.stage_count} stage{template.stage_count !== 1 ? 's' : ''} · {template.last_used}
      </div>
    </button>
  )
}
