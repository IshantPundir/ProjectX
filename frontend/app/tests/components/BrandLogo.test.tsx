import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { BrandLogo, BrandMark } from "@/components/px";
import { brand } from "@/lib/brand";

describe("BrandLogo", () => {
  it("renders the wordmark with the brand name as alt text", () => {
    render(<BrandLogo height={32} />);
    const img = screen.getByAltText(brand.name);
    expect(img).toBeInTheDocument();
  });
});

describe("BrandMark", () => {
  it("renders the square mark with the brand name as alt text", () => {
    render(<BrandMark size={26} />);
    const img = screen.getByAltText(brand.name);
    expect(img).toBeInTheDocument();
  });
});
