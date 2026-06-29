import * as React from "react";
import { cn } from "@/lib/utils";

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  mono?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ className, mono = false, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn("px-input", mono && "mono", className)}
        {...rest}
      />
    );
  },
);
