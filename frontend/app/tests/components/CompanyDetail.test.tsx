import * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CompanyDetail } from "@/app/(dashboard)/settings/org-units/[unitId]/CompanyDetail";
import type { OrgUnit } from "@/lib/api/org-units";

// Mock the update hook so we can assert on the payload it receives.
const mutateAsync = vi.fn();
vi.mock("@/lib/hooks/use-update-org-unit", () => ({
  useUpdateOrgUnit: () => ({ mutateAsync, isPending: false }),
}));
vi.mock("@/lib/hooks/use-me", () => ({
  useMe: () => ({ data: { is_super_admin: true, assignments: [] } }),
  canManageUnit: () => true,
}));
vi.mock("@/lib/hooks/use-pipeline-templates", () => ({
  usePipelineTemplates: () => ({ data: [], isLoading: false }),
}));
// Mock Sidebar + SidebarMembersCard to avoid router/auth dependencies.
vi.mock(
  "@/app/(dashboard)/settings/org-units/[unitId]/Sidebar",
  () => ({ Sidebar: () => null }),
);
vi.mock(
  "@/app/(dashboard)/settings/org-units/[unitId]/SidebarMembersCard",
  () => ({ SidebarMembersCard: () => null }),
);
// Mock next/navigation (Sidebar and sub-components call useRouter).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

function makeUnit(overrides: Partial<OrgUnit> = {}): OrgUnit {
  return {
    id: "u1",
    client_id: "t1",
    parent_unit_id: null,
    name: "Acme",
    unit_type: "client_account",
    member_count: 0,
    created_at: "2026-05-14T00:00:00Z",
    created_by: null,
    created_by_email: null,
    deletable_by: null,
    deletable_by_email: null,
    admin_delete_disabled: false,
    is_accessible: true,
    admin_emails: [],
    is_root: false,
    about: null,
    industry: null,
    hiring_bar: null,
    website: null,
    country: null,
    state: null,
    city: null,
    company_profile_completed_at: null,
    company_profile_completion_status: "pending",
    metadata: null,
    inherited_address: null,
    ...overrides,
  };
}

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("CompanyDetail", () => {
  it("renders Industry row in the header", () => {
    renderWithQuery(
      <CompanyDetail
        unit={makeUnit({ industry: "Banking / Financial Services" })}
        isClientAccount
        parentChain={[]}
        subUnits={[]}
        openRolesCount={0}
        openRolesByChildId={{}}
        onBack={() => {}}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByTestId("unit-industry-row")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("Banking / Financial Services"),
    ).toBeInTheDocument();
  });

  it("renders Address block with inheritance label when local value is null", () => {
    renderWithQuery(
      <CompanyDetail
        unit={makeUnit({
          country: null,
          state: null,
          city: null,
          inherited_address: {
            values: { country: "US", state: "NY", city: null },
            source_unit_id: "ancestor1",
          },
        })}
        isClientAccount
        parentChain={[
          {
            ...makeUnit({ id: "ancestor1", name: "Acme HQ", unit_type: "company" }),
          },
        ]}
        subUnits={[]}
        openRolesCount={0}
        openRolesByChildId={{}}
        onBack={() => {}}
        onSaved={() => {}}
      />,
    );
    expect(screen.getAllByText(/Inherited from Acme HQ/i).length).toBeGreaterThan(0);
  });

  it("saves about with blank industry — sends correct payload, no all-or-nothing gate", async () => {
    mutateAsync.mockResolvedValueOnce(makeUnit({ about: "new about text" }));
    renderWithQuery(
      <CompanyDetail
        unit={makeUnit()}
        isClientAccount
        parentChain={[]}
        subUnits={[]}
        openRolesCount={0}
        openRolesByChildId={{}}
        onBack={() => {}}
        onSaved={() => {}}
      />,
    );

    // Enter edit mode.
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    // Type into About; leave Industry blank.
    const aboutTextarea = screen.getByLabelText(/About/i);
    fireEvent.change(aboutTextarea, {
      target: { value: "new about text" },
    });

    // Save.
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const body = mutateAsync.mock.calls[0][0].body;
    expect(body.about).toBe("new about text");
    expect(body.set_about).toBe(true);
    expect(body.set_industry).toBe(true);
    expect(body.industry).toBe("");
  });
});
