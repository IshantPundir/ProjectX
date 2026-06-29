'use client'

import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import '../recordings-theme.css'

/**
 * Public recordings route — its own minimal QueryClient boundary.
 * Wraps children in a daylight-theme container so the vendored
 * report components render with the same token set as the recruiter app.
 */
export default function RecordingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const [qc] = useState(() => new QueryClient())

  // The report theaters render in dialogs that createPortal to document.body
  // (px/Dialog + the VideoControls violation hover-card), escaping the in-tree
  // daylight wrapper below. On the candidate session app <html> is
  // data-px-theme="cool-light", so daylight-only CSS vars (--px-ok-fill,
  // --px-danger-fill, …) would be undefined for that portaled content →
  // colorless gauges / pills / proctoring-timeline. Scope the whole route to
  // daylight on <body> (which every body-portal inherits) for this layout's
  // lifetime, restoring on unmount so other routes (e.g. /interview) keep
  // their own theme. Portals are client-only, so this runs before they mount.
  useEffect(() => {
    const previous = document.body.getAttribute('data-px-theme')
    document.body.setAttribute('data-px-theme', 'daylight')
    return () => {
      if (previous === null) document.body.removeAttribute('data-px-theme')
      else document.body.setAttribute('data-px-theme', previous)
    }
  }, [])

  return (
    <QueryClientProvider client={qc}>
      <div data-px-theme="daylight" className="recordings-root min-h-dvh">
        {children}
      </div>
    </QueryClientProvider>
  )
}
