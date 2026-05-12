"use client";

type Props = {
  processed: number;
  total: number;
};

export function SyncProgressBar({ processed, total }: Props) {
  if (total === 0) return null;

  const indeterminate = total < 0;
  const pct = indeterminate
    ? 0
    : Math.min(100, Math.round((processed / total) * 100));

  return (
    <div className="space-y-1">
      <div
        role="progressbar"
        aria-busy={indeterminate}
        aria-valuemin={0}
        aria-valuemax={indeterminate ? undefined : total}
        aria-valuenow={indeterminate ? undefined : processed}
        className="relative h-2 w-full overflow-hidden rounded-full"
        style={
          {
            background: "color-mix(in oklab, var(--px-fg) 8%, transparent)",
            "--fill": indeterminate ? "30%" : `${pct}%`,
          } as React.CSSProperties
        }
      >
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] ${
            indeterminate ? "animate-pulse" : ""
          }`}
          style={{
            width: "var(--fill)",
            background: "var(--px-accent)",
          }}
        />
      </div>
      <div className="text-xs text-zinc-500">
        {indeterminate ? (
          <span>Counting jobs…</span>
        ) : (
          <span>
            {processed} / {total} ({pct}%)
          </span>
        )}
      </div>
    </div>
  );
}
