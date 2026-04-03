import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

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
          if (payload.tenant_id && payload.app_role) {
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

  // Check that user has tenant_id + app_role (rejects admin-only accounts)
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    try {
      const base64 = session.access_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
          const payload = JSON.parse(atob(base64));
      if (!payload.tenant_id || !payload.app_role) {
        // User is authenticated but not a client user (e.g., admin-only account)
        // Sign them out of this app and redirect to login
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
