import type { NextConfig } from "next";

// Security headers applied to every response.
//
// CSP is included here because the candidate surface has no Supabase
// (no inline-bootstrap-script complexity), no React Query devtools,
// and no third-party SDK that requires a nonce. The frontend/app CSP
// is deferred separately due to those constraints.
//
// Referrer-Policy: no-referrer is LOAD-BEARING — prevents the candidate
// JWT (in URL path) from leaking via Referer headers to external links.
//
// connect-src includes the LiveKit Cloud wildcard. For self-hosted
// LiveKit deployments, parameterize via env at that point.
const API_URL = process.env.NEXT_PUBLIC_API_URL;
if (!API_URL) {
  throw new Error(
    "NEXT_PUBLIC_API_URL must be set at build time — it is embedded in the Content-Security-Policy header. " +
    "For Docker builds: pass --build-arg NEXT_PUBLIC_API_URL=https://api.example.com. " +
    "For local builds: source .env.local before running next build.",
  );
}

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob: mediastream:",
  `connect-src 'self' ${API_URL} wss://*.livekit.cloud https://*.livekit.cloud`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
  "upgrade-insecure-requests",
].join("; ");

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
  { key: "Content-Security-Policy", value: CSP },
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
