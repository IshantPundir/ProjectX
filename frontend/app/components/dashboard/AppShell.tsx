"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import type { MeResponse } from "@/lib/api/auth";
import { isAnyAdmin } from "@/lib/hooks/use-me";

/* ─── Icons ────────────────────────────────────────────────── */

type IconPath = string | readonly string[];

function ShIcon({
  d,
  size = 16,
  stroke = 1.6,
  fill = "none",
  className = "",
}: {
  d: IconPath;
  size?: number;
  stroke?: number;
  fill?: string;
  className?: string;
}) {
  const paths = Array.isArray(d) ? d : [d as string];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={fill}
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flexShrink: 0 }}
      aria-hidden="true"
    >
      {paths.map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  );
}

const NI = {
  home: "M3 10l9-7 9 7v10a2 2 0 01-2 2h-4v-8H9v8H5a2 2 0 01-2-2z",
  briefcase: [
    "M20 7H4a2 2 0 00-2 2v10a2 2 0 002 2h16a2 2 0 002-2V9a2 2 0 00-2-2z",
    "M16 21V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v16",
  ] as const,
  users: [
    "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2",
    "M9 11a4 4 0 100-8 4 4 0 000 8z",
    "M23 21v-2a4 4 0 00-3-3.87",
    "M16 3.13a4 4 0 010 7.75",
  ] as const,
  kanban: ["M3 3h7v18H3zM14 3h7v12h-7z"] as const,
  layers: [
    "M12 2L2 7l10 5 10-5-10-5z",
    "M2 17l10 5 10-5",
    "M2 12l10 5 10-5",
  ] as const,
  chart: ["M3 3v18h18", "M7 14l4-4 4 4 5-5"] as const,
  settings: [
    "M12 15a3 3 0 100-6 3 3 0 000 6z",
    "M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3h.1a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8v.1a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
  ] as const,
  bell: [
    "M18 8a6 6 0 00-12 0c0 7-3 9-3 9h18s-3-2-3-9",
    "M13.7 21a2 2 0 01-3.4 0",
  ] as const,
  search: ["M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3"] as const,
  chevL: "M15 6l-6 6 6 6",
  chevR: "M9 6l6 6-6 6",
  sparkle:
    "M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8",
  book: "M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z",
  tree: [
    "M12 2v6",
    "M8 8h8",
    "M4 14a2 2 0 104 0 2 2 0 00-4 0z",
    "M10 14a2 2 0 104 0 2 2 0 00-4 0z",
    "M16 14a2 2 0 104 0 2 2 0 00-4 0z",
    "M6 12V8M12 12V8M18 12V8",
  ] as const,
  user: [
    "M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2",
    "M12 11a4 4 0 100-8 4 4 0 000 8z",
  ] as const,
  plug: [
    "M9 2v6",
    "M15 2v6",
    "M6 8h12v4a6 6 0 01-12 0z",
    "M12 18v4",
  ] as const,
  logOut: [
    "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4",
    "M16 17l5-5-5-5",
    "M21 12H9",
  ] as const,
};

/* ─── Nav items ────────────────────────────────────────────── */

type NavItem = {
  href: string;
  label: string;
  icon: IconPath;
  kbd?: string;
  /**
   * Optional visibility predicate. When present and returns false, the
   * item is not rendered in the rail. Used to gate tenant-wide admin
   * surfaces (Org units, Team & access) to admins only — pages enforce
   * the same rule independently so direct URL navigation also lands on
   * the access-denied panel.
   */
  visible?: (me: MeResponse | null | undefined) => boolean;
};

// Primary nav matches the v4 design. Pipeline + Question bank are
// top-level "power-user" surfaces that pick a role (and for QB a stage)
// before landing on the per-role detail view. Reports is a planned
// placeholder with no design artboard yet.
const PRIMARY_NAV: readonly NavItem[] = [
  { href: "/", label: "Home", icon: NI.home, kbd: "H" },
  { href: "/jobs", label: "Roles", icon: NI.briefcase, kbd: "R" },
  { href: "/candidates", label: "Candidates", icon: NI.users, kbd: "C" },
  { href: "/tracker", label: "Tracker", icon: NI.kanban, kbd: "T" },
  { href: "/pipeline", label: "Pipeline", icon: NI.layers, kbd: "P" },
  { href: "/questions", label: "Question bank", icon: NI.book, kbd: "Q" },
  { href: "/reports", label: "Reports", icon: NI.chart },
] as const;

const FOOTER_NAV: readonly NavItem[] = [
  {
    href: "/settings/org-units",
    label: "Org units",
    icon: NI.tree,
    visible: isAnyAdmin,
  },
  {
    href: "/settings/team",
    label: "Team & access",
    icon: NI.users,
    visible: isAnyAdmin,
  },
  {
    href: "/settings/integrations",
    label: "Integrations",
    icon: NI.plug,
    visible: isAnyAdmin,
  },
  { href: "/profile", label: "Profile", icon: NI.user },
] as const;

/* ─── Breadcrumbs from pathname ────────────────────────────── */

const PATH_LABELS: Record<string, string> = {
  "": "Home",
  jobs: "Roles",
  new: "New",
  candidates: "Candidates",
  tracker: "Tracker",
  pipeline: "Pipeline",
  questions: "Question bank",
  reports: "Reports",
  settings: "Settings",
  "org-units": "Org units",
  team: "Team & access",
  integrations: "Integrations",
  profile: "Profile",
  review: "JD review",
  "company-profile": "Company profile",
  "pipeline-templates": "Pipeline templates",
};

function humanizeSlug(slug: string) {
  if (PATH_LABELS[slug]) return PATH_LABELS[slug];
  if (/^[0-9a-f-]{8,}$/i.test(slug)) return "Detail";
  return slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function useCrumbs(pathname: string): string[] {
  return useMemo(() => {
    const segs = pathname.split("/").filter(Boolean);
    if (segs.length === 0) return ["Home"];
    return segs.map(humanizeSlug);
  }, [pathname]);
}

/* ─── NavItem renderer ─────────────────────────────────────── */

function NavLink({
  item,
  active,
  collapsed,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
}) {
  return (
    <Link
      href={item.href}
      title={collapsed ? item.label : undefined}
      aria-current={active ? "page" : undefined}
      className="group relative flex items-center gap-2.5 rounded-md transition-colors"
      style={{
        height: 32,
        padding: collapsed ? "0 7px" : "0 10px",
        background: active ? "var(--px-surface-2)" : "transparent",
        color: active ? "var(--px-fg)" : "var(--px-fg-2)",
        fontSize: 13,
        fontWeight: active ? 500 : 400,
      }}
    >
      {active && (
        <span
          className="absolute left-[-8px] top-[6px] bottom-[6px] w-[2px] rounded-sm"
          style={{ background: "var(--px-accent)" }}
          aria-hidden="true"
        />
      )}
      <ShIcon d={item.icon} size={15} stroke={active ? 1.8 : 1.5} />
      {!collapsed && (
        <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
          {item.label}
        </span>
      )}
      {!collapsed && item.kbd ? (
        <span
          className="px-kbd"
          style={{ marginLeft: "auto", opacity: active ? 1 : 0.5 }}
        >
          {item.kbd}
        </span>
      ) : null}
    </Link>
  );
}

/* ─── AppShell ─────────────────────────────────────────────── */

export interface AppShellProps {
  userEmail: string;
  userName?: string;
  userRole?: string;
  orgContext?: string;
  aiChip?: string;
  /** Server-fetched /me payload; null when /me failed (rendered as a
   *  graceful degradation so the shell still mounts). Drives nav-item
   *  visibility for admin-only surfaces. */
  me?: MeResponse | null;
  children: React.ReactNode;
}

export function AppShell({
  userEmail,
  userName,
  userRole = "Recruiter",
  orgContext,
  aiChip,
  me,
  children,
}: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const crumbs = useCrumbs(pathname);

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(href + "/");
  };

  async function handleSignOut() {
    setSigningOut(true);
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  const initials = (userName || userEmail)
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() ?? "")
    .join("") || "U";

  const railWidth = collapsed ? 54 : 220;

  return (
    <div
      className="flex h-full w-full"
      style={{ background: "var(--px-bg)", color: "var(--px-fg)" }}
    >
      {/* ─── Nav rail ─── */}
      <aside
        // sticky + self-start + h-screen together keep the rail pinned full
        // viewport-height regardless of how the parent scrolls (body-level
        // scroll, internal scroll container, or a mix of both).
        //
        // No right border — the rounded top-left corner on the content area
        // provides the visible seam, letting the rail + top bar read as one
        // continuous chrome surface.
        //
        // `data-appshell-rail` is a stable query hook so pages with pinned
        // master-detail layouts (e.g. /jobs/[id]/questions) can measure
        // the rail's right edge and extend their aside flush to it.
        data-appshell-rail=""
        className="sticky top-0 self-start flex h-screen flex-shrink-0 flex-col overflow-hidden"
        style={{
          width: railWidth,
          background: "var(--px-bg-2)",
          transition: "width 180ms cubic-bezier(0.2, 0.8, 0.3, 1)",
          zIndex: 10,
        }}
      >
        {/* Brand */}
        <div
          className="flex flex-shrink-0 items-center gap-2.5 px-3.5"
          style={{ height: 52 }}
        >
          <div
            className="flex h-[26px] w-[26px] flex-shrink-0 items-center justify-center rounded-md"
            style={{ background: "var(--px-accent)" }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M3 2v8l5-4z" fill="#fff" />
            </svg>
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <div
                className="text-[13px] font-semibold leading-tight"
                style={{ color: "var(--px-fg)" }}
              >
                ProjectX
              </div>
              {orgContext && (
                <div
                  className="truncate text-[10.5px] leading-tight"
                  style={{ color: "var(--px-fg-4)" }}
                >
                  {orgContext}
                </div>
              )}
            </div>
          )}
          {!collapsed && (
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              title="Collapse"
              aria-label="Collapse sidebar"
              className="flex h-[22px] w-[22px] cursor-pointer items-center justify-center rounded border-none bg-transparent"
              style={{ color: "var(--px-fg-3)" }}
            >
              <ShIcon d={NI.chevL} size={13} />
            </button>
          )}
        </div>

        {collapsed && (
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            title="Expand"
            aria-label="Expand sidebar"
            className="mx-auto my-1 flex h-[26px] w-full cursor-pointer items-center justify-center border-none bg-transparent"
            style={{ color: "var(--px-fg-3)" }}
          >
            <ShIcon d={NI.chevR} size={13} />
          </button>
        )}

        {/* Primary items */}
        <nav
          aria-label="Main navigation"
          className="flex flex-col gap-px px-2 py-2.5"
        >
          {PRIMARY_NAV.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              active={isActive(item.href)}
              collapsed={collapsed}
            />
          ))}
        </nav>

        <div className="flex-1" />

        {/* Footer items */}
        <div
          className="flex flex-col gap-px border-t px-2 pb-2 pt-2.5"
          style={{ borderColor: "var(--px-hairline)" }}
        >
          {FOOTER_NAV.filter(
            (item) => item.visible == null || item.visible(me),
          ).map((item) => (
            <NavLink
              key={item.href}
              item={item}
              active={isActive(item.href)}
              collapsed={collapsed}
            />
          ))}
        </div>

        {/* User chip */}
        <div
          className="flex flex-shrink-0 items-center gap-2.5 border-t px-3.5"
          style={{ height: 52, borderColor: "var(--px-hairline)" }}
        >
          <div
            className="flex h-[26px] w-[26px] flex-shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
            style={{
              background: "var(--px-accent-tint)",
              color: "var(--px-accent)",
            }}
          >
            {initials}
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <div
                className="truncate text-[12px] font-medium leading-tight"
                style={{ color: "var(--px-fg)" }}
                title={userName || userEmail}
              >
                {userName || userEmail}
              </div>
              <div
                className="truncate text-[10.5px] leading-tight"
                style={{ color: "var(--px-fg-4)" }}
              >
                {userRole}
              </div>
            </div>
          )}
          {!collapsed && (
            <button
              type="button"
              onClick={handleSignOut}
              disabled={signingOut}
              title={signingOut ? "Signing out…" : "Sign out"}
              aria-label="Sign out"
              className="flex h-[22px] w-[22px] cursor-pointer items-center justify-center rounded border-none bg-transparent transition-colors disabled:opacity-50"
              style={{ color: "var(--px-fg-3)" }}
            >
              <ShIcon d={NI.logOut} size={13} />
            </button>
          )}
        </div>
      </aside>

      {/* ─── Main column ─── */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Top bar — sticky so it stays visible regardless of scroll level.
            No bottom border — the content area below carries a rounded
            top-left + top/left hairlines that trace the chrome/content seam
            into a single smooth L-shape. */}
        <header
          className="sticky top-0 flex h-[var(--px-topbar-h,48px)] flex-shrink-0 items-center gap-3 px-4"
          style={{
            background: "var(--px-bg-2)",
            zIndex: 9,
          }}
        >
          {/* Concave corner painter — fills the chrome's inner L-corner with
              a curve that mirrors the content area's rounded top-left, so
              nav rail + top bar + content read as one smooth shape. Sits
              just below the top bar, anchored to its bottom-left. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute"
            style={{
              top: "100%",
              left: 0,
              width: 16,
              height: 16,
              background:
                "radial-gradient(circle at bottom right, transparent 0, transparent 16px, var(--px-bg-2) 16px)",
            }}
          />

          {/* Breadcrumbs */}
          <nav
            aria-label="Breadcrumb"
            className="flex flex-shrink-0 items-center gap-1.5 text-[13px]"
            style={{ color: "var(--px-fg-3)" }}
          >
            {crumbs.map((crumb, i) => (
              <span key={i} className="flex items-center gap-1.5">
                {i > 0 && <ShIcon d={NI.chevR} size={10} />}
                <span
                  style={{
                    color:
                      i === crumbs.length - 1
                        ? "var(--px-fg)"
                        : "var(--px-fg-3)",
                    fontWeight: i === crumbs.length - 1 ? 500 : 400,
                  }}
                >
                  {crumb}
                </span>
              </span>
            ))}
          </nav>

          <div className="flex-1" />

          {/* Search trigger */}
          <button
            type="button"
            className="flex cursor-pointer items-center gap-2 rounded-md border text-[12px]"
            style={{
              height: 28,
              minWidth: 200,
              padding: "0 8px 0 10px",
              background: "var(--px-surface-2)",
              borderColor: "var(--px-hairline)",
              color: "var(--px-fg-3)",
            }}
          >
            <ShIcon d={NI.search} size={12} />
            <span className="flex-1 text-left">Search or jump to…</span>
            <span className="px-kbd">⌘K</span>
          </button>

          {/* Copilot chip */}
          {aiChip && (
            <span className="px-copilot-strip cursor-pointer">
              <ShIcon d={NI.sparkle} size={11} />
              {aiChip}
            </span>
          )}

          {/* Notifications */}
          <button
            type="button"
            aria-label="Notifications"
            className="relative flex h-7 w-7 cursor-pointer items-center justify-center rounded-md border-none bg-transparent"
            style={{ color: "var(--px-fg-3)" }}
          >
            <ShIcon d={NI.bell} size={14} />
            <span
              className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full"
              style={{ background: "var(--px-accent)" }}
              aria-hidden="true"
            />
          </button>
        </header>

        {/* Page content — no overflow-auto so the body remains the scroll
            container. A scroll context here would trap sticky descendants
            (Sections, Copilot) to a non-scrolling ancestor and unpin them
            from the actual scroll. Rounded top-left + top/left hairlines
            still curve the chrome/content seam into a single smooth L. */}
        <div
          className="min-h-0 flex-1 border-l border-t rounded-tl-2xl"
          style={{
            background: "var(--px-bg)",
            borderColor: "var(--px-hairline)",
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
