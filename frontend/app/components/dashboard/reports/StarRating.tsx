'use client'

const STAR_PATH = 'M12 2l3 6.5 7 .8-5.2 4.8 1.4 6.9L12 17.6 5.8 21l1.4-6.9L2 9.3l7-.8z'

export function StarRating({ valueTen, size = 18 }: { valueTen: number; size?: number }) {
  const stars = Math.max(0, Math.min(5, valueTen / 2))
  const label = `${String(Math.round(stars * 2) / 2).replace(/\.0$/, '')} out of 5`
  return (
    <span role="img" aria-label={label} className="inline-flex gap-[3px]">
      {Array.from({ length: 5 }, (_, i) => {
        const fill = Math.max(0, Math.min(1, stars - i)) // 0, .5, or 1
        return (
          <svg key={i} width={size} height={size} viewBox="0 0 24 24" aria-hidden>
            <defs>
              <linearGradient id={`g${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ffd24d" /><stop offset="100%" stopColor="#f5a623" />
              </linearGradient>
              <clipPath id={`c${i}`}><rect x="0" y="0" width={24 * fill} height="24" /></clipPath>
            </defs>
            <path d={STAR_PATH} fill="none" stroke="#d9d9e3" strokeWidth="1.4" />
            {fill > 0 && <path d={STAR_PATH} fill={`url(#g${i})`} clipPath={`url(#c${i})`} />}
          </svg>
        )
      })}
    </span>
  )
}
