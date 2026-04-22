import Link from 'next/link'

type ErrorCode =
  | 'TOKEN_EXPIRED'
  | 'TOKEN_SUPERSEDED'
  | 'TOKEN_ALREADY_USED'
  | 'UNKNOWN'

const MESSAGES: Record<ErrorCode, { title: string; body: string }> = {
  TOKEN_EXPIRED: {
    title: 'This link has expired',
    body: 'Please ask the recruiter to resend your interview invite.',
  },
  TOKEN_SUPERSEDED: {
    title: 'This link is no longer valid',
    body: 'A newer invite has been sent to your inbox. Please use that one.',
  },
  TOKEN_ALREADY_USED: {
    title: 'This session has already started',
    body: 'If you need to rejoin, contact the recruiter.',
  },
  UNKNOWN: {
    title: 'Something went wrong',
    body: 'Please contact the recruiter who sent you this invite.',
  },
}

function isErrorCode(value: string | undefined): value is ErrorCode {
  return (
    value === 'TOKEN_EXPIRED' ||
    value === 'TOKEN_SUPERSEDED' ||
    value === 'TOKEN_ALREADY_USED' ||
    value === 'UNKNOWN'
  )
}

export default async function InterviewErrorPage({
  searchParams,
}: {
  searchParams: Promise<{ code?: string }>
}) {
  const { code } = await searchParams
  const resolved: ErrorCode = isErrorCode(code) ? code : 'UNKNOWN'
  const m = MESSAGES[resolved]
  return (
    <div className="mx-auto max-w-[640px] px-8 py-24 text-center">
      <h1
        className="px-serif m-0 text-[40px] font-normal"
        style={{ letterSpacing: '-1px', color: 'var(--px-fg)' }}
      >
        {m.title}
      </h1>
      <p
        className="mx-auto mt-4 max-w-md text-[15px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
      >
        {m.body}
      </p>
      <Link
        href="/"
        className="mt-8 inline-block text-sm underline"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Go to homepage
      </Link>
    </div>
  )
}
