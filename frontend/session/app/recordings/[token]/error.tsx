'use client'

export default function Error() {
  return (
    <div className="mx-auto max-w-md p-10 text-center">
      <h1 className="text-lg font-semibold">This link is no longer available</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The shared recording link may have expired or been revoked.
      </p>
    </div>
  )
}
