'use client'

const ITEMS = [
  { label: 'Company', color: 'bg-blue-500' },
  { label: 'Division', color: 'bg-violet-500' },
  { label: 'Client Account', color: 'bg-emerald-500' },
  { label: 'Region', color: 'bg-orange-500' },
  { label: 'Team', color: 'bg-amber-500' },
]

export function OrgUnitLegend() {
  return (
    <div className="absolute top-3 left-3 bg-white/90 backdrop-blur border border-zinc-200 rounded-lg px-3 py-2 shadow-sm z-10">
      <div className="text-[9px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5">
        Types
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {ITEMS.map((item) => (
          <div key={item.label} className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full ${item.color}`}
              aria-hidden="true"
            />
            <span className="text-[10px] text-zinc-600">{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
