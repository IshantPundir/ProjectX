import type { NextConfig } from "next";

// Security headers applied to every response.
//
// CSP is intentionally NOT set here — adding it requires nonce wiring
// across server components, inline scripts (Next.js bootstrap, React Query
// devtools, Tailwind v4), and third-party SDKs (Supabase, LiveKit). That
// work is a follow-up. Until then, these header-level mitigations give us
// clickjacking, MIME-sniffing, referrer-leak, and feature-policy defense.
//
// The Permissions-Policy allows camera + microphone from same-origin for
// the Phase 3D AI Copilot panel (human interviewers join live sessions
// from this surface). Geolocation is explicitly denied. The candidate
// interview surface itself moved to frontend/session/.
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
  // Dev-only. In scripts/demo.sh LAN mode the dashboard is reached at the host's
  // private LAN IP (not localhost) so a shared-report-PDF recipient on the same
  // WiFi can open the public /recordings/<token> page. Next dev blocks
  // cross-origin requests to its /_next/* dev resources, and Turbopack loads the
  // app's modules via Origin-bearing module-script fetches — so on a non-localhost
  // origin every chunk 403s and the page renders blank. Allow the RFC-1918 LAN
  // ranges in dev. Ignored entirely in production (the dev-resource block never
  // runs there). Mirrors frontend/session's allowedDevOrigins: ["*.ngrok-free.app"].
  allowedDevOrigins: ["192.168.*.*", "10.*.*.*"],
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
