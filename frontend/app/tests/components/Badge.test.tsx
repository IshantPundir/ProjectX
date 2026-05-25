import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "@/components/px/Badge";

describe("Badge", () => {
  it("applies the human variant class", () => {
    render(<Badge variant="human">Borderline</Badge>);
    expect(screen.getByText("Borderline")).toHaveClass("px-badge", "human");
  });

  it("renders a dot when dot is set (color is not the only signal)", () => {
    const { container } = render(<Badge variant="ok" dot>Strong</Badge>);
    expect(container.querySelector(".px-dot")).not.toBeNull();
  });

  it("renders no dot by default", () => {
    const { container } = render(<Badge variant="ok">Strong</Badge>);
    expect(container.querySelector(".px-dot")).toBeNull();
  });
});
