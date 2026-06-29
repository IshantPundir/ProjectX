"use client";

import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type CheckboxProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
};

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  function Checkbox({ label, className, id, ...props }, ref) {
    const inputId = id ?? `cb-${label.replace(/\s+/g, "-").toLowerCase()}`;
    return (
      <label
        htmlFor={inputId}
        className={cn(
          "flex items-center gap-2 cursor-pointer text-sm",
          className,
        )}
        style={{ color: "var(--px-fg)" }}
      >
        <input
          ref={ref}
          type="checkbox"
          id={inputId}
          className="size-4"
          style={{ accentColor: "var(--px-accent)" }}
          {...props}
        />
        <span>{label}</span>
      </label>
    );
  },
);
