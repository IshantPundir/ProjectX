import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "@/components/px/Button";

describe("Button loading", () => {
  it("disables the button and shows a spinner when loading", () => {
    render(<Button loading>Save</Button>);
    const btn = screen.getByRole("button", { name: /save/i });
    expect(btn).toBeDisabled();
    expect(btn.querySelector("svg")).not.toBeNull();
  });

  it("is not disabled when not loading", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: /save/i })).not.toBeDisabled();
  });

  it("keeps an explicit disabled even when not loading", () => {
    render(<Button disabled>Save</Button>);
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });
});
