import Link from "next/link";
import { createClient } from "@/lib/supabase/server";

/* ─── Attention card ─────────────────────────────────────── */

function AttentionCard({
  eyebrow,
  accent,
  title,
  body,
  meta,
  cta,
}: {
  eyebrow: string;
  accent: string;
  title: string;
  body: string;
  meta: string;
  cta: string;
}) {
  return (
    <div
      className="relative flex flex-col gap-2.5 overflow-hidden rounded-[10px] border p-[18px]"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <span
        className="absolute bottom-0 left-0 top-0 w-[2px]"
        style={{ background: accent }}
        aria-hidden="true"
      />
      <div
        className="text-[10px] font-semibold uppercase"
        style={{ letterSpacing: "1.2px", color: accent }}
      >
        {eyebrow}
      </div>
      <div
        className="text-[15px] font-semibold leading-snug"
        style={{ color: "var(--px-fg)" }}
      >
        {title}
      </div>
      <div
        className="flex-1 text-[12.5px]"
        style={{ color: "var(--px-fg-3)", lineHeight: 1.55 }}
      >
        {body}
      </div>
      <div
        className="flex items-center gap-2 border-t pt-2 text-[11px]"
        style={{
          borderColor: "var(--px-hairline)",
          color: "var(--px-fg-4)",
        }}
      >
        <span>{meta}</span>
        <div className="flex-1" />
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors"
          style={{
            borderColor: "var(--px-hairline-strong)",
            background: "var(--px-surface)",
            color: "var(--px-fg-2)",
          }}
        >
          {cta} →
        </button>
      </div>
    </div>
  );
}

/* ─── Role status dot ─────────────────────────────────────── */

function RoleStatus({ status }: { status: "live" | "reviewing" | "draft" | "paused" }) {
  const map = {
    live: { label: "live", color: "var(--px-ok)" },
    reviewing: { label: "reviewing", color: "var(--px-accent)" },
    draft: { label: "draft", color: "var(--px-fg-4)" },
    paused: { label: "paused", color: "var(--px-caution)" },
  } as const;
  const v = map[status];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-1.5 text-[10px] font-medium"
      style={{
        height: 18,
        letterSpacing: "0.2px",
        borderColor: "var(--px-hairline)",
        color: v.color,
      }}
    >
      <span
        className="h-[5px] w-[5px] rounded-full"
        style={{ background: v.color }}
      />
      {v.label}
    </span>
  );
}

/* ─── Active roles list ───────────────────────────────────── */

const ROLE_ROWS = [
  { title: "Staff Backend Engineer", status: "reviewing", cands: 14, inFlight: 2, pass: 4, trend: "+2", hm: "Alex Chen" },
  { title: "Senior Frontend Engineer", status: "live", cands: 21, inFlight: 5, pass: 7, trend: "+5", hm: "Noor Patel" },
  { title: "Platform Engineer", status: "live", cands: 8, inFlight: 3, pass: 2, trend: "+1", hm: "Alex Chen" },
  { title: "Staff ML Engineer", status: "draft", cands: 0, inFlight: 0, pass: 0, trend: "—", hm: "Sam Rivera" },
] as const;

function ActiveRoles() {
  return (
    <div
      className="overflow-hidden rounded-[10px] border"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div className="flex items-center gap-2.5 px-4 pb-2.5 pt-3.5">
        <h2
          className="m-0 text-sm font-semibold"
          style={{ color: "var(--px-fg)" }}
        >
          Your active roles
        </h2>
        <span
          className="px-mono text-[11px]"
          style={{ color: "var(--px-fg-4)" }}
        >
          {ROLE_ROWS.length}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          className="text-[11px] font-medium"
          style={{ color: "var(--px-fg-3)" }}
        >
          See all →
        </button>
      </div>
      <div style={{ borderTop: "1px solid var(--px-hairline)" }}>
        {ROLE_ROWS.map((r, i) => (
          <div
            key={r.title}
            className="grid cursor-pointer items-center gap-3 px-4 text-[12.5px] transition-colors hover:brightness-95"
            style={{
              gridTemplateColumns: "1fr 72px 72px 72px 60px",
              padding: "11px 16px",
              borderBottom:
                i < ROLE_ROWS.length - 1
                  ? "1px solid var(--px-hairline)"
                  : "none",
            }}
          >
            <div className="flex min-w-0 items-center gap-2">
              <RoleStatus status={r.status} />
              <span
                className="truncate font-medium"
                style={{ color: "var(--px-fg)" }}
              >
                {r.title}
              </span>
              <span className="text-[11.5px]" style={{ color: "var(--px-fg-4)" }}>
                · {r.hm}
              </span>
            </div>
            <div
              className="px-mono text-right text-[12px]"
              style={{ color: "var(--px-fg-2)", fontVariantNumeric: "tabular-nums" }}
            >
              {r.cands}
            </div>
            <div
              className="px-mono text-right text-[12px]"
              style={{ color: "var(--px-fg-2)", fontVariantNumeric: "tabular-nums" }}
            >
              {r.inFlight}
            </div>
            <div
              className="px-mono text-right text-[12px]"
              style={{ color: "var(--px-fg-2)", fontVariantNumeric: "tabular-nums" }}
            >
              {r.pass}
            </div>
            <div
              className="px-mono text-right text-[11px]"
              style={{
                color: r.trend.startsWith("+") ? "var(--px-ok)" : "var(--px-fg-4)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {r.trend}
            </div>
          </div>
        ))}
        <div
          className="grid items-center gap-3 px-4 py-2 text-[10px] uppercase"
          style={{
            gridTemplateColumns: "1fr 72px 72px 72px 60px",
            letterSpacing: "0.5px",
            color: "var(--px-fg-4)",
            borderTop: "1px solid var(--px-hairline)",
            background: "var(--px-bg-2)",
          }}
        >
          <span />
          <span className="text-right">Cands</span>
          <span className="text-right">Active</span>
          <span className="text-right">Passed</span>
          <span className="text-right">7d</span>
        </div>
      </div>
    </div>
  );
}

/* ─── Today's pipeline ─────────────────────────────────────── */

const TODAY_STAGES = [
  { name: "Applied", n: 8, hot: false },
  { name: "Screening", n: 5, hot: true },
  { name: "Interview", n: 3, hot: true },
  { name: "Offer", n: 1, hot: false },
] as const;

function TodayPipeline() {
  const max = Math.max(...TODAY_STAGES.map((s) => s.n));
  return (
    <div
      className="rounded-[10px] border p-4"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div className="mb-3.5 flex items-baseline gap-2">
        <h2 className="m-0 text-sm font-semibold" style={{ color: "var(--px-fg)" }}>
          Today
        </h2>
        <span className="text-[11.5px]" style={{ color: "var(--px-fg-4)" }}>
          · 4 active roles
        </span>
      </div>
      <div className="flex flex-col gap-3">
        {TODAY_STAGES.map((s) => (
          <div key={s.name}>
            <div className="mb-1.5 flex items-baseline gap-2">
              <span
                className="text-[12px] font-medium"
                style={{ color: "var(--px-fg-2)" }}
              >
                {s.name}
              </span>
              {s.hot && (
                <span
                  className="px-mono text-[10px]"
                  style={{ color: "var(--px-accent)" }}
                >
                  moving
                </span>
              )}
              <div className="flex-1" />
              <span
                className="px-mono text-[13px] font-medium"
                style={{ color: "var(--px-fg)", fontVariantNumeric: "tabular-nums" }}
              >
                {s.n}
              </span>
            </div>
            <div
              className="h-1 overflow-hidden rounded-full"
              style={{ background: "var(--px-surface-3)" }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(s.n / max) * 100}%`,
                  background: s.hot ? "var(--px-accent)" : "var(--px-fg-3)",
                }}
              />
            </div>
          </div>
        ))}
      </div>
      <div
        className="mt-4 flex items-center gap-2 border-t pt-3 text-[11.5px]"
        style={{ borderColor: "var(--px-hairline)", color: "var(--px-fg-3)" }}
      >
        <span>17 candidates in motion</span>
        <div className="flex-1" />
        <Link
          className="cursor-pointer text-[11.5px] font-medium"
          style={{ color: "var(--px-accent)" }}
          href="/jobs"
        >
          Open pipeline →
        </Link>
      </div>
    </div>
  );
}

/* ─── Activity feed ────────────────────────────────────────── */

type ActivityItem = {
  who: string;
  what: string;
  when: string;
  ai?: boolean;
  score?: "strong";
};

const ACTIVITY: ActivityItem[] = [
  { who: "Maya Chen", what: "completed Staff BE interview", score: "strong", when: "2m" },
  { who: "Copilot", what: "drafted debrief for Maya Chen", when: "2m", ai: true },
  { who: "Alex Chen", what: "approved JD for Platform Engineer", when: "38m" },
  { who: "Jordan Park", what: "opened invite (2nd time)", when: "1h" },
  { who: "Copilot", what: "inferred 4 new signals from Staff ML draft", when: "2h", ai: true },
  { who: "Noor Patel", what: "commented on Senior FE · Q3", when: "4h" },
];

function Activity() {
  return (
    <div
      className="rounded-[10px] border p-4"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div className="mb-3.5 flex items-baseline gap-2">
        <h2 className="m-0 text-sm font-semibold" style={{ color: "var(--px-fg)" }}>
          Activity
        </h2>
        <div className="flex-1" />
        <button
          type="button"
          className="text-[11px] font-medium"
          style={{ color: "var(--px-fg-3)" }}
        >
          All →
        </button>
      </div>
      <div className="flex flex-col gap-3">
        {ACTIVITY.map((a, i) => (
          <div key={i} className="flex items-start gap-2.5">
            <span
              className="flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-full border text-[9px] font-semibold"
              style={{
                background: a.ai ? "var(--px-accent-tint)" : "var(--px-surface-2)",
                color: a.ai ? "var(--px-accent)" : "var(--px-fg-3)",
                borderColor: "var(--px-hairline)",
              }}
            >
              {a.ai ? (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
                </svg>
              ) : (
                <span>
                  {a.who
                    .split(" ")
                    .map((n) => n[0])
                    .join("")
                    .slice(0, 2)}
                </span>
              )}
            </span>
            <div className="flex-1 text-[12.5px]" style={{ lineHeight: 1.55 }}>
              <span className="font-medium" style={{ color: "var(--px-fg)" }}>
                {a.who}
              </span>
              <span style={{ color: "var(--px-fg-3)" }}> {a.what}</span>
              {a.score === "strong" && (
                <span
                  className="px-chip ok ml-1.5"
                  style={{ height: 16, padding: "0 5px", fontSize: "9.5px", letterSpacing: "0.3px" }}
                >
                  strong
                </span>
              )}
            </div>
            <span
              className="px-mono flex-shrink-0 text-[10.5px]"
              style={{ color: "var(--px-fg-4)" }}
            >
              {a.when}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Copilot brief ────────────────────────────────────────── */

const COPILOT_ITEMS = [
  {
    t: "Maya Chen's debrief is ready.",
    s: "She was strong on distributed systems; I've drafted talking points for the panel.",
  },
  {
    t: "Jordan Park might be stalling.",
    s: "Two invite opens, no progress in 3 days. I can draft a nudge that matches your voice.",
  },
  {
    t: "Staff ML JD is a little thin.",
    s: "You might want to add 2–3 competencies before it goes to the hiring manager.",
  },
];

function CopilotBrief() {
  return (
    <div
      className="rounded-[10px] border p-4"
      style={{
        background:
          "linear-gradient(180deg, var(--px-accent-tint) 0%, var(--px-surface) 60%)",
        borderColor: "var(--px-accent-line)",
      }}
    >
      <div className="mb-3 flex items-center gap-2">
        <span
          className="flex h-[22px] w-[22px] items-center justify-center rounded-full"
          style={{ background: "var(--px-accent)", color: "#fff" }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
          </svg>
        </span>
        <h2 className="m-0 text-sm font-semibold" style={{ color: "var(--px-fg)" }}>
          Copilot brief
        </h2>
        <span className="text-[11px]" style={{ color: "var(--px-fg-4)" }}>
          · updated 4m ago
        </span>
      </div>

      <div
        className="mb-3.5 text-[13px]"
        style={{ color: "var(--px-fg-2)", lineHeight: 1.6 }}
      >
        Three things I&apos;d look at first:
      </div>

      <div className="flex flex-col gap-2">
        {COPILOT_ITEMS.map((x) => (
          <div
            key={x.t}
            className="cursor-pointer rounded-lg border px-3 py-2.5"
            style={{
              background: "var(--px-surface)",
              borderColor: "var(--px-hairline)",
            }}
          >
            <div
              className="mb-0.5 text-[12.5px] font-medium"
              style={{ color: "var(--px-fg)" }}
            >
              {x.t}
            </div>
            <div
              className="text-[11.5px]"
              style={{ color: "var(--px-fg-3)", lineHeight: 1.5 }}
            >
              {x.s}
            </div>
          </div>
        ))}
      </div>

      <div
        className="mt-3.5 flex items-center gap-2.5 border-t pt-3"
        style={{ borderColor: "var(--px-accent-line)" }}
      >
        <span
          className="text-[11px] italic"
          style={{ color: "var(--px-fg-3)" }}
        >
          Based on the last 24h across 4 roles.
        </span>
        <div className="flex-1" />
        <button
          type="button"
          className="text-[11px] font-medium"
          style={{ color: "var(--px-fg-3)" }}
        >
          Ask something →
        </button>
      </div>
    </div>
  );
}

/* ─── Page ─────────────────────────────────────────────────── */

function getGreetingName(email: string): string {
  const local = email.split("@")[0] ?? "";
  const first = local.split(/[._-]/)[0] ?? "";
  if (!first) return "there";
  return first.charAt(0).toUpperCase() + first.slice(1);
}

function getTimeOfDay(): "morning" | "afternoon" | "evening" {
  const h = new Date().getHours();
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}

export default async function DashboardPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const name = getGreetingName(user?.email ?? "");
  const tod = getTimeOfDay();

  return (
    <div className="mx-auto max-w-[1200px] px-8 pb-10 pt-7">
      {/* Greeting */}
      <div className="mb-7">
        <h1
          className="px-serif m-0 text-[34px] font-normal"
          style={{ letterSpacing: "-0.8px", color: "var(--px-fg)" }}
        >
          Good {tod}, {name}.
        </h1>
        <div
          className="mt-1.5 text-sm"
          style={{ color: "var(--px-fg-3)" }}
        >
          You have{" "}
          <b className="font-semibold" style={{ color: "var(--px-fg)" }}>
            5 things
          </b>{" "}
          to look at today. None of them are urgent — but three are moving.
        </div>
      </div>

      {/* Attention cards */}
      <section className="mb-8">
        <div className="grid grid-cols-1 gap-3.5 md:grid-cols-3">
          <AttentionCard
            eyebrow="JD review"
            accent="var(--px-ai)"
            title="Staff Backend Engineer"
            body="Copilot extracted 17 signals, 2 to double-check."
            meta="Ready to approve · 4m ago"
            cta="Review signals"
          />
          <AttentionCard
            eyebrow="Candidate"
            accent="var(--px-ok)"
            title="Maya Chen completed her interview"
            body="Strong on distributed systems, medium on PG sharding. Copilot drafted a debrief."
            meta="Staff BE · 2m ago"
            cta="Open debrief"
          />
          <AttentionCard
            eyebrow="Candidate"
            accent="var(--px-caution)"
            title="Jordan Park hasn't started in 3 days"
            body="Invite sent Monday, opened twice, no progress."
            meta="Senior FE · follow-up?"
            cta="Draft nudge"
          />
        </div>
      </section>

      {/* Roles + today */}
      <section className="mb-8 grid grid-cols-1 gap-5 lg:grid-cols-[1.4fr_1fr]">
        <ActiveRoles />
        <TodayPipeline />
      </section>

      {/* Activity + copilot */}
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Activity />
        <CopilotBrief />
      </section>
    </div>
  );
}
