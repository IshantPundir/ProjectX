'use client'

export function CompletionScreen() {
  return (
    <div className="min-h-screen grid place-items-center bg-zinc-50 px-6">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold text-zinc-900">
          Thanks for completing your interview.
        </h1>
        <p className="mt-3 text-zinc-600">You can close this tab. We&apos;ll be in touch soon.</p>
      </div>
    </div>
  )
}
