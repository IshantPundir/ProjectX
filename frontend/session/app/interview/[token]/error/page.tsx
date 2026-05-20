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
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-[28px] font-normal text-px-fg" style={{ letterSpacing: '-0.5px' }}>
          {m.title}
        </h1>
        <p className="mx-auto mt-3 max-w-sm text-[15px] text-px-fg-3" style={{ lineHeight: 1.7 }}>
          {m.body}
        </p>
        <Link href="/" className="mt-8 inline-block text-sm text-px-fg-4 underline">
          Go to homepage
        </Link>
      </div>
    </div>
  )
}
