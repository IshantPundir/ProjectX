"use client";

import * as React from "react";

export interface ComboboxOption {
  /** Optional group label — items with the same group render under one heading. */
  group?: string;
  value: string;
  /** Human-readable label shown in the popover. */
  label: string;
  /** Right-aligned secondary text (e.g. "GMT−7" for timezones). */
  secondary?: string;
}

export interface ComboboxProps {
  options: ComboboxOption[];
  value: string;
  onChange: (next: string) => void;
  /** Values pinned under "Common" at the top of the popover when the
   *  search field is empty. Useful for surfacing the user's likely
   *  picks (browser timezone, popular currencies, etc.). Values not
   *  present in `options` are silently skipped. */
  commonValues?: string[];
  /** Section heading for the pinned items. Defaults to "Common". */
  commonLabel?: string;
  searchPlaceholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  /** Trigger render-prop. Receives `triggerProps` (apply to the
   *  clickable element), `isOpen`, and the selected option. */
  children: (api: {
    triggerProps: {
      ref: React.Ref<HTMLButtonElement>;
      onClick: () => void;
      onKeyDown: (e: React.KeyboardEvent) => void;
      "aria-haspopup": "listbox";
      "aria-expanded": boolean;
      disabled: boolean;
    };
    isOpen: boolean;
    selected: ComboboxOption | undefined;
  }) => React.ReactNode;
}

/**
 * Lightweight searchable combobox. Renders the trigger via a render-prop
 * so the consumer can keep its existing chrome (e.g. our `.locale-chip`
 * styling), and renders the popover as an absolutely-positioned sibling
 * of a wrapper element. Filter is substring-match across label, value,
 * group, and secondary fields. Keyboard: ↑/↓ navigate, Enter selects,
 * Esc closes.
 *
 * Designed for ≤500 options — no virtualization. If we ever need more,
 * lift `react-window` in here.
 */
export function Combobox({
  options,
  value,
  onChange,
  commonValues,
  commonLabel = "Common",
  searchPlaceholder = "Search…",
  ariaLabel,
  disabled,
  className,
  children,
}: ComboboxProps) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [highlight, setHighlight] = React.useState(0);

  const wrapperRef = React.useRef<HTMLSpanElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const listRef = React.useRef<HTMLDivElement>(null);
  const listboxId = React.useId();

  const selected = React.useMemo(
    () => options.find((o) => o.value === value),
    [options, value],
  );

  // Filtered + grouped option list. When there is no query and we have
  // common values, render a synthetic "Common" group at the top.
  const flat = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = q
      ? options.filter((o) => {
          return (
            o.value.toLowerCase().includes(q) ||
            o.label.toLowerCase().includes(q) ||
            (o.group?.toLowerCase().includes(q) ?? false) ||
            (o.secondary?.toLowerCase().includes(q) ?? false)
          );
        })
      : options;

    const out: Array<
      | { kind: "header"; key: string; label: string }
      | { kind: "option"; key: string; option: ComboboxOption }
    > = [];

    if (!q && commonValues && commonValues.length > 0) {
      const seen = new Set<string>();
      const common: ComboboxOption[] = [];
      for (const v of commonValues) {
        if (seen.has(v)) continue;
        seen.add(v);
        const opt = options.find((o) => o.value === v);
        if (opt) common.push(opt);
      }
      if (common.length > 0) {
        out.push({ kind: "header", key: "__common", label: commonLabel });
        for (const opt of common) {
          out.push({
            kind: "option",
            key: `common:${opt.value}`,
            option: opt,
          });
        }
      }
    }

    let lastGroup: string | undefined = undefined;
    let firstGroup = true;
    for (const opt of matches) {
      if (opt.group !== lastGroup) {
        if (opt.group) {
          out.push({
            kind: "header",
            key: `g:${opt.group}-${firstGroup ? "first" : "next"}`,
            label: opt.group,
          });
        }
        lastGroup = opt.group;
        firstGroup = false;
      }
      out.push({ kind: "option", key: opt.value, option: opt });
    }
    return out;
  }, [options, query, commonValues, commonLabel]);

  const optionRows = React.useMemo(
    () =>
      flat
        .map((row, i) => (row.kind === "option" ? i : -1))
        .filter((i) => i >= 0),
    [flat],
  );

  // When the popover opens, focus the search and seed the highlight on
  // the currently selected value (or the first option).
  React.useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const targetIndex = optionRows.find((i) => {
      const row = flat[i];
      return row.kind === "option" && row.option.value === value;
    });
    setHighlight(targetIndex ?? optionRows[0] ?? 0);
  }, [open, optionRows, flat, value]);

  // Reset query on close so the next open starts clean.
  React.useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  // Close on outside click.
  React.useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (
        wrapperRef.current &&
        !wrapperRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  // Keep the highlighted option scrolled into view.
  React.useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-row-index="${highlight}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function moveHighlight(dir: 1 | -1) {
    if (optionRows.length === 0) return;
    const currentIdx = optionRows.indexOf(highlight);
    const nextIdx =
      currentIdx === -1
        ? dir === 1
          ? 0
          : optionRows.length - 1
        : (currentIdx + dir + optionRows.length) % optionRows.length;
    setHighlight(optionRows[nextIdx]);
  }

  function commitHighlight() {
    const row = flat[highlight];
    if (row?.kind === "option") {
      onChange(row.option.value);
      setOpen(false);
      // Return focus to the trigger so keyboard users land on a known spot.
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }

  function onTriggerKeyDown(e: React.KeyboardEvent) {
    if (disabled) return;
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    }
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveHighlight(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveHighlight(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      commitHighlight();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      requestAnimationFrame(() => triggerRef.current?.focus());
    } else if (e.key === "Home") {
      e.preventDefault();
      if (optionRows.length > 0) setHighlight(optionRows[0]);
    } else if (e.key === "End") {
      e.preventDefault();
      if (optionRows.length > 0) setHighlight(optionRows[optionRows.length - 1]);
    }
  }

  return (
    <span ref={wrapperRef} className={`combobox-wrapper${className ? ` ${className}` : ""}`}>
      {children({
        triggerProps: {
          ref: triggerRef,
          onClick: () => !disabled && setOpen((v) => !v),
          onKeyDown: onTriggerKeyDown,
          "aria-haspopup": "listbox",
          "aria-expanded": open,
          disabled: !!disabled,
        },
        isOpen: open,
        selected,
      })}
      {open && (
        <div
          className="combobox-popover"
          role="dialog"
          aria-label={ariaLabel}
        >
          <div className="combobox-search">
            <SearchIcon />
            <input
              ref={inputRef}
              type="text"
              className="combobox-search-input"
              placeholder={searchPlaceholder}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onInputKeyDown}
              role="combobox"
              aria-controls={listboxId}
              aria-expanded={true}
              aria-autocomplete="list"
              aria-activedescendant={
                flat[highlight]?.kind === "option"
                  ? `${listboxId}-${highlight}`
                  : undefined
              }
            />
          </div>
          <div
            ref={listRef}
            id={listboxId}
            role="listbox"
            aria-label={ariaLabel}
            className="combobox-list"
          >
            {flat.length === 0 && (
              <div className="combobox-empty">No matches.</div>
            )}
            {flat.map((row, i) => {
              if (row.kind === "header") {
                return (
                  <div key={row.key} className="combobox-group-header">
                    {row.label}
                  </div>
                );
              }
              const opt = row.option;
              const isSelected = opt.value === value;
              const isHighlighted = i === highlight;
              return (
                <div
                  key={row.key}
                  id={`${listboxId}-${i}`}
                  role="option"
                  aria-selected={isSelected}
                  data-row-index={i}
                  className={`combobox-option${isHighlighted ? " is-highlighted" : ""}${isSelected ? " is-selected" : ""}`}
                  onMouseEnter={() => setHighlight(i)}
                  onClick={() => {
                    onChange(opt.value);
                    setOpen(false);
                    requestAnimationFrame(() => triggerRef.current?.focus());
                  }}
                >
                  <span className="combobox-option-label">{opt.label}</span>
                  {opt.secondary && (
                    <span className="combobox-option-secondary">
                      {opt.secondary}
                    </span>
                  )}
                  {isSelected && (
                    <span
                      className="combobox-option-check"
                      aria-hidden="true"
                    >
                      ✓
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </span>
  );
}

function SearchIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}
