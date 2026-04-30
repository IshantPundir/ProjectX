'use client'

const COPY: Record<string, { title: string; body: string }> = {
  AGENT_NO_SHOW: {
    title: "Interviewer didn't connect",
    body: "We couldn't reach the interviewer. Please try again later or contact your recruiter.",
  },
  MEDIA_LOST: {
    title: 'Camera or microphone unavailable',
    body: 'Your camera or microphone is no longer accessible. Please reconnect to continue.',
  },
}

export function DisconnectError({ code }: { code: string }) {
  const c = COPY[code] ?? { title: 'Session disconnected', body: 'An unexpected error occurred.' }
  return (
    <div className="min-h-screen grid place-items-center bg-zinc-50 px-6">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold text-zinc-900">{c.title}</h1>
        <p className="mt-2 text-sm text-zinc-600">{c.body}</p>
        <p className="mt-6 text-xs text-zinc-500">Error code: {code}</p>
      </div>
    </div>
  )
}
