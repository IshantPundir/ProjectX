"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/**
 * Dialog — portal-based modal with focus trap, ESC to close, scroll lock.
 *
 * API mirrors shadcn: controlled via `open` + `onOpenChange`. Supports the
 * Dialog / DialogContent / DialogHeader / DialogTitle / DialogDescription /
 * DialogFooter / DialogClose surface. `DialogTrigger` is a thin wrapper that
 * attaches an onClick to open the parent dialog via context.
 */

type DialogContextValue = {
  open: boolean;
  setOpen: (next: boolean) => void;
  labelledBy?: string;
  describedBy?: string;
  registerLabelledBy: (id: string) => void;
  registerDescribedBy: (id: string) => void;
};

const DialogContext = React.createContext<DialogContextValue | null>(null);

function useDialog(): DialogContextValue {
  const ctx = React.useContext(DialogContext);
  if (!ctx) {
    throw new Error("Dialog sub-component used outside <Dialog>");
  }
  return ctx;
}

export interface DialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: React.ReactNode;
}

export function Dialog({
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  children,
}: DialogProps) {
  const [uncontrolled, setUncontrolled] = React.useState(defaultOpen);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolled;
  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setUncontrolled(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  const [labelledBy, setLabelledBy] = React.useState<string | undefined>();
  const [describedBy, setDescribedBy] = React.useState<string | undefined>();

  const ctx = React.useMemo<DialogContextValue>(
    () => ({
      open,
      setOpen,
      labelledBy,
      describedBy,
      registerLabelledBy: setLabelledBy,
      registerDescribedBy: setDescribedBy,
    }),
    [open, setOpen, labelledBy, describedBy],
  );

  return <DialogContext.Provider value={ctx}>{children}</DialogContext.Provider>;
}

/** Optional trigger that opens the dialog on click. */
export function DialogTrigger({
  children,
  asChild: _asChild,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { asChild?: boolean }) {
  const { setOpen } = useDialog();
  return (
    <button type="button" onClick={() => setOpen(true)} {...rest}>
      {children}
    </button>
  );
}

/** Close button. Renders as a native button by default. */
export function DialogClose({
  children,
  onClick,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { setOpen } = useDialog();
  return (
    <button
      type="button"
      onClick={(e) => {
        onClick?.(e);
        if (!e.defaultPrevented) setOpen(false);
      }}
      {...rest}
    >
      {children}
    </button>
  );
}

/* ─── Focus-trap helpers ─── */

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function getFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute("data-focus-guard"),
  );
}

/* ─── Content ─── */

export interface DialogContentProps extends React.HTMLAttributes<HTMLDivElement> {
  showCloseButton?: boolean;
  /** Max-width helper. Defaults to `sm:max-w-md`. */
  widthClass?: string;
}

export function DialogContent({
  className,
  children,
  showCloseButton = true,
  widthClass = "sm:max-w-md",
  ...rest
}: DialogContentProps) {
  const { open, setOpen, labelledBy, describedBy } = useDialog();
  const popupRef = React.useRef<HTMLDivElement>(null);
  const previouslyFocused = React.useRef<HTMLElement | null>(null);
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  // Scroll lock + focus management while open.
  React.useEffect(() => {
    if (!open) return;

    previouslyFocused.current =
      typeof document !== "undefined"
        ? (document.activeElement as HTMLElement | null)
        : null;

    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusInitial = () => {
      const popup = popupRef.current;
      if (!popup) return;
      const focusables = getFocusable(popup);
      if (focusables.length > 0) {
        focusables[0].focus();
      } else {
        popup.focus();
      }
    };
    // Defer to next frame so portal children render before focus.
    const raf = requestAnimationFrame(focusInitial);

    return () => {
      cancelAnimationFrame(raf);
      document.body.style.overflow = originalOverflow;
      const prev = previouslyFocused.current;
      if (prev && typeof prev.focus === "function") {
        // Defer so the closing transition doesn't fight focus restore.
        requestAnimationFrame(() => prev.focus());
      }
    };
  }, [open]);

  // ESC + Tab trap.
  React.useEffect(() => {
    if (!open) return;

    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      const popup = popupRef.current;
      if (!popup) return;
      const focusables = getFocusable(popup);
      if (focusables.length === 0) {
        e.preventDefault();
        popup.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !popup.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, setOpen]);

  if (!mounted || !open) return null;

  const node = (
    <div
      className="px-dialog-backdrop"
      onMouseDown={(e) => {
        // Close on backdrop click only (not clicks inside the content).
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div
        ref={popupRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        className={cn("px-dialog-content", widthClass, className)}
        {...rest}
      >
        {children}
        {showCloseButton && (
          <DialogClose aria-label="Close" className="px-dialog-close">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </DialogClose>
        )}
      </div>
    </div>
  );

  return createPortal(node, document.body);
}

/* ─── Layout helpers ─── */

export function DialogHeader({
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-dialog-header", className)} {...rest} />;
}

export function DialogFooter({
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-dialog-footer", className)} {...rest} />;
}

let titleAutoId = 0;
let descAutoId = 0;

export function DialogTitle({
  className,
  id,
  children,
  ...rest
}: React.HTMLAttributes<HTMLHeadingElement>) {
  const { registerLabelledBy } = useDialog();
  const autoId = React.useMemo(() => id ?? `px-dialog-title-${++titleAutoId}`, [id]);
  React.useEffect(() => {
    registerLabelledBy(autoId);
  }, [autoId, registerLabelledBy]);
  return (
    <h2 id={autoId} className={cn("px-dialog-title", className)} {...rest}>
      {children}
    </h2>
  );
}

export function DialogDescription({
  className,
  id,
  children,
  ...rest
}: React.HTMLAttributes<HTMLParagraphElement>) {
  const { registerDescribedBy } = useDialog();
  const autoId = React.useMemo(() => id ?? `px-dialog-desc-${++descAutoId}`, [id]);
  React.useEffect(() => {
    registerDescribedBy(autoId);
  }, [autoId, registerDescribedBy]);
  return (
    <p id={autoId} className={cn("px-dialog-description", className)} {...rest}>
      {children}
    </p>
  );
}
