import type { NextConfig } from "next";

// Build-time guard: NEXT_PUBLIC_API_URL is the candidate-session backend
// origin. proxy.ts also throws at request time, but failing here surfaces
// misconfiguration during `next build` (Docker / Railway / ECS) rather than
// in production logs after deploy.
if (!process.env.NEXT_PUBLIC_API_URL) {
  throw new Error(
    "NEXT_PUBLIC_API_URL must be set at build time. " +
    "For Docker builds: pass --build-arg NEXT_PUBLIC_API_URL=https://api.example.com. " +
    "For local builds: source .env.local before running next build.",
  );
}

// STATIC security headers applied to every response.
//
// CSP is NOT here — it lives in proxy.ts because it requires a
// per-request nonce. Static `script-src 'self'` would block Next.js's
// own inline bootstrap script. See proxy.ts for the nonce-based CSP.
//
// Referrer-Policy: no-referrer is LOAD-BEARING — prevents the candidate
// JWT (in URL path) from leaking via Referer headers to external links.
const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=(), interest-cohort=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
      },
    ];
  },
};

export default nextConfig;
