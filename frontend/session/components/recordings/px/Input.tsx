import * as React from "react";
import { cn } from "@/lib/utils";

export type InputSize = "default" | "sm" | "lg";

export interface InputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "size"> {
  inputSize?: InputSize;
  mono?: boolean;
}

const SIZE_CLASS: Record<InputSize, string> = {
  default: "",
  sm: "sm",
  lg: "lg",
};

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  function Input(
    { className, type = "text", inputSize = "default", mono = false, ...rest },
    ref,
  ) {
    return (
      <input
        ref={ref}
        type={type}
        className={cn("px-input", SIZE_CLASS[inputSize], mono && "mono", className)}
        {...rest}
      />
    );
  },
);
