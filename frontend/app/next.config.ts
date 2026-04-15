import type { NextConfig } from "next";

// Security headers applied to every response.
//
// CSP is intentionally NOT set here — adding it requires nonce wiring
// across server components, inline scripts (Next.js bootstrap, React Query
// devtools, Tailwind v4), and third-party SDKs (Supabase, LiveKit). That
// work is a follow-up. Until then, these header-level mitigations give us
// clickjacking, MIME-sniffing, referrer-leak, and feature-policy defense.
//
// The Permissions-Policy allows camera + microphone from same-origin
// because candidate interview sessions require them; geolocation is
// explicitly denied.
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=()",
  },
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
