"use client";

import { Toaster as Sonner, type ToasterProps } from "sonner";

export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="light"
      className="toaster group"
      style={
        {
          "--normal-bg": "var(--px-surface)",
          "--normal-text": "var(--px-fg)",
          "--normal-border": "var(--px-hairline-strong)",
          "--border-radius": "var(--px-r-md)",
          fontFamily: "var(--font-sans)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
}
