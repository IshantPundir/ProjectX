// lib/brand.ts
// Minimal product-identity config for the candidate surface. The recruiter app
// (frontend/app/lib/brand.ts) is the fuller source; this is the candidate-side
// subset (name + logo only — no theme/density config needed here). Keep the
// logo asset shape compatible if the two are ever reconciled.

export interface LogoAsset {
  /** Path under public/ */
  src: string
  /** Intrinsic pixel dimensions — required by next/image. */
  width: number
  height: number
}

export interface SessionBrand {
  /** Full product name — used as logo alt text + prose. */
  name: string
  /** Compact name for tight inline contexts. */
  shortName: string
  logo: { wordmark: LogoAsset; mark: LogoAsset }
}

export const brand: SessionBrand = {
  name: 'BinQle.ai',
  shortName: 'BinQle',
  logo: {
    wordmark: { src: '/brand/binqle-wordmark.png', width: 960, height: 263 },
    mark: { src: '/brand/binqle-mark.png', width: 256, height: 256 },
  },
}
