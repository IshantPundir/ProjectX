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
  // HSTS is PRODUCTION-ONLY. In dev the app is served over plain http on
  // localhost; sending HSTS there is pointless (browsers ignore HSTS received
  // over http) AND dangerous if it ever sticks — HSTS is host-scoped, so a
  // pinned `localhost` force-upgrades every `ws://localhost:*` to `wss://`,
  // which the plaintext self-hosted LiveKit SFU (ws on :7880) can't answer,
  // breaking the candidate's room connection. Emit it only in production
  // (where every origin is already TLS).
  ...(process.env.NODE_ENV === "production"
    ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" }]
    : []),
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
  // Dev-only: Next 16 blocks /_next/* and HMR requests from non-localhost
  // origins, which breaks LAN demos via ngrok. The wildcard covers rotating
  // ngrok-free.app subdomains. No effect in production (`next dev` only).
  allowedDevOrigins: ["*.ngrok-free.app"],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
      },
      {
        // Public recordings share page — never index a capability URL.
        source: "/recordings/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }],
      },
    ];
  },
};

export default nextConfig;
