import { MousePointerClick } from 'lucide-react'

export function EmptyInspectorState() {
  return (
    <div className="h-full flex items-center justify-center p-12">
      <div className="text-center max-w-sm">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-zinc-100 mb-4">
          <MousePointerClick
            className="w-5 h-5 text-zinc-400"
            aria-hidden="true"
          />
        </div>
        <h3 className="text-base font-semibold text-zinc-900 mb-2">
          Select a stage to get started
        </h3>
        <p className="text-sm text-zinc-500">
          Click any stage in the pipeline flow on the left to review its
          questions and configuration.
        </p>
      </div>
    </div>
  )
}
