import type { NextConfig } from "next";

// Security headers applied to every response.
//
// CSP is intentionally NOT set here — adding it requires nonce wiring
// across server components and inline scripts. That work is a follow-up.
// Until then, these header-level mitigations give us clickjacking,
// MIME-sniffing, referrer-leak, and feature-policy defense.
//
// The Permissions-Policy denies camera, microphone, and geolocation:
// the internal admin tool has no legitimate need for any of them.
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=()",
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
