/**
 * Suspense fallback for the JD-review route. Required because the page
 * (and the inner JDReviewShell) call useSearchParams() in client
 * components — without a loading boundary, Next 16 prints a hydration
 * warning and the route opts out of static rendering.
 */
export default function Loading() {
  return (
    <div
      className="px-6 pb-4 pt-5 text-sm"
      style={{ color: 'var(--px-fg-3)' }}
    >
      Loading…
    </div>
  )
}
