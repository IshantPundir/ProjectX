// lib/brand.ts
// Single source of truth for product identity and the active look.
// Change the name/logo here; change colors in app/theme.css.

export type ThemeName = "warm-light"; // add new names here as themes are added to app/theme.css
export type DensityName = "compact" | "comfortable" | "spacious";

export interface LogoAsset {
  /** Path under public/ */
  src: string;
  /** Intrinsic pixel dimensions (post-trim) — required by next/image. */
  width: number;
  height: number;
}

export interface BrandConfig {
  /** Full product name — browser tab title + prose. */
  name: string;
  /** Compact name for tight inline contexts. */
  shortName: string;
  /** Metadata description. */
  tagline: string;
  /** Login screen sub-headline. */
  loginSubtitle: string;
  logo: { wordmark: LogoAsset; mark: LogoAsset };
  /** Active look. Must match a [data-px-theme="…"] block in app/theme.css. */
  theme: ThemeName;
  /** Active density. */
  density: DensityName;
}

export const brand = {
  name: "BinQle.ai",
  shortName: "BinQle",
  tagline: "AI Video Interview Platform",
  loginSubtitle: "Sign in to your recruiting dashboard",
  logo: {
    wordmark: { src: "/brand/binqle-wordmark.png", width: 960, height: 263 },
    mark: { src: "/brand/binqle-mark.png", width: 256, height: 256 },
  },
  theme: "warm-light",
  density: "comfortable",
} as const satisfies BrandConfig;
