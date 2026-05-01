import type { NextConfig } from "next";

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
