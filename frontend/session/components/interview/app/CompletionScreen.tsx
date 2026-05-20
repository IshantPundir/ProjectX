'use client'

export function CompletionScreen() {
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-2xl text-px-fg">Thanks — your interview&apos;s complete.</h1>
        <p className="mt-3 text-sm text-px-fg-3">
          You can close this tab now. We&apos;ll be in touch with next steps soon.
        </p>
      </div>
    </div>
  )
}
