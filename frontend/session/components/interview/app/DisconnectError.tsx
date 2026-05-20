'use client'

const COPY: Record<string, { title: string; body: string }> = {
  AGENT_NO_SHOW: {
    title: "Interviewer didn't connect",
    body: "We couldn't reach the interviewer. Please try again later or contact your recruiter.",
  },
  CANDIDATE_UNRESPONSIVE: {
    title: "We didn't hear from you",
    body: "We ended the interview because we couldn't hear from you for a while. If this was unexpected, please contact your recruiter.",
  },
  MEDIA_LOST: {
    title: 'Camera or microphone unavailable',
    body: 'Your camera or microphone is no longer accessible. Please reconnect to continue.',
  },
  SESSION_ALREADY_STARTED: {
    title: 'This session has already started',
    body: "You've already started this interview. If you were disconnected, please contact your recruiter.",
  },
  SESSION_START_FAILED: {
    title: 'Could not start the interview',
    body: 'Something went wrong starting your interview. Please refresh and try again.',
  },
  ENGINE_ERROR: {
    title: 'The interviewer encountered an error',
    body: 'Your interview was interrupted. Please contact your recruiter.',
  },
  UNEXPECTED_DISCONNECT: {
    title: 'Connection lost',
    body: "We lost the connection unexpectedly. Please contact your recruiter if this persists.",
  },
  RECONNECT_FAILED: {
    title: 'Connection lost',
    body: "We tried to reconnect but couldn't. Please contact your recruiter.",
  },
  SESSION_ALREADY_COMPLETED: {
    title: 'This interview is already complete',
    body: 'Your interview has already finished. Thank you — we will be in touch.',
  },
  DUPLICATE_SESSION: {
    title: 'Disconnected — another tab took over',
    body: 'Your interview is now running in another browser tab. Close other tabs and try again from there.',
  },
  TOKEN_EXPIRED: {
    title: 'This invite link has expired',
    body: 'Your invite link is no longer valid. Please contact your recruiter for a new link.',
  },
  REJOIN_RATE_LIMITED: {
    title: 'Too many rejoin attempts',
    body: "You've tried to rejoin too many times in a short window. Please wait a few minutes and try again.",
  },
  REJOIN_REJECTED: {
    title: 'Could not rejoin the interview',
    body: 'We could not rejoin your interview. Please contact your recruiter.',
  },
}

export function DisconnectError({ code }: { code: string }) {
  const c = COPY[code] ?? { title: 'Session disconnected', body: 'An unexpected error occurred.' }
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-xl text-px-fg">{c.title}</h1>
        <p className="mt-2 text-sm text-px-fg-3">{c.body}</p>
        <p className="mt-6 font-mono text-xs text-px-fg-4">Error code: {code}</p>
      </div>
    </div>
  )
}
