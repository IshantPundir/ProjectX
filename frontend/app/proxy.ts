import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

// Tight UUID v4-shaped pattern. The looser /^[0-9a-f-]{36}$/i would have
// accepted nonsense like 36 dashes; this matches only properly-grouped
// UUIDs, which is the only thing the app ever puts in the legacy `jd` URL.
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Translate the legacy `/candidates?jd=<uuid>&view=kanban` URL to the new
 * `/tracker/<uuid>` surface. Returns the target URL when a redirect should
 * fire, or null otherwise.
 *
 * The UUID regex guards against open-redirect via crafted `jd` values.
 * Mirrors the redirect-allowlist pattern used by `app/(auth)/invite/page.tsx`.
 *
 * Exported so the rule is unit-testable without spinning up a NextRequest.
 */
export function checkLegacyRedirect(url: URL): URL | null {
  if (
    url.pathname === '/candidates' &&
    url.searchParams.get('view') === 'kanban'
  ) {
    const jd = url.searchParams.get('jd') ?? ''
    const target = UUID_RE.test(jd) ? `/tracker/${jd}` : '/tracker'
    return new URL(target, url)
  }
  return null
}

const PUBLIC_PATHS = new Set(["/login"]);

export async function proxy(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const path = request.nextUrl.pathname;

  const legacy = checkLegacyRedirect(request.nextUrl)
  if (legacy) {
    return NextResponse.redirect(legacy, 308)
  }

  // Invite pages are always public (no auth needed)
  if (path.startsWith("/invite")) {
    await supabase.auth.getUser();
    return supabaseResponse;
  }

  // Validate session
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  // /login — redirect to dashboard if already logged in with valid tenant
  if (path === "/login") {
    if (user && !error) {
      // Check if this user has a tenant_id (i.e., is a client user, not admin-only)
      const { data: { session } } = await supabase.auth.getSession();
      if (session?.access_token) {
        try {
          const base64 = session.access_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
          const payload = JSON.parse(atob(base64));
          if (payload.tenant_id) {
            return NextResponse.redirect(new URL("/", request.url));
          }
        } catch {
          // Token parse failed — fall through to login page
        }
      }
    }
    return supabaseResponse;
  }

  // Not authenticated → login
  if (error || !user) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // Check that user has tenant_id (rejects admin-only accounts with no tenant)
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    try {
      const base64 = session.access_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      const payload = JSON.parse(atob(base64));
      if (!payload.tenant_id) {
        // User is authenticated but not a client user (e.g., admin-only account)
        await supabase.auth.signOut();
        return NextResponse.redirect(new URL("/login", request.url));
      }
    } catch {
      return NextResponse.redirect(new URL("/login", request.url));
    }
  }

  return supabaseResponse;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
