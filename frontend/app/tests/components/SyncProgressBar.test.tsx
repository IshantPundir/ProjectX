import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SyncProgressBar } from "@/components/settings/integrations/SyncProgressBar";

describe("SyncProgressBar", () => {
  it("renders processed / total with percentage", () => {
    render(<SyncProgressBar processed={245} total={662} />);
    expect(screen.getByText(/245 \/ 662/)).toBeInTheDocument();
    expect(screen.getByText(/37%/)).toBeInTheDocument();
  });

  it("renders nothing when total is 0", () => {
    const { container } = render(<SyncProgressBar processed={0} total={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders indeterminate (aria-busy) when total is -1", () => {
    render(<SyncProgressBar processed={0} total={-1} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-busy", "true");
  });

  it("caps fill at 100% when processed > total (defensive)", () => {
    render(<SyncProgressBar processed={700} total={662} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.style.getPropertyValue("--fill")).toBe("100%");
  });
});
