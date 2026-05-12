import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobStatusFilterDialog } from "@/components/settings/integrations/JobStatusFilterDialog";

vi.mock("@/lib/auth/tokens", () => ({
  getFreshSupabaseToken: vi.fn(async () => "tok"),
}));

const listJobStatusesMock = vi.fn();
const updateJobStatusFilterMock = vi.fn();
vi.mock("@/lib/api/ats", async () => {
  const actual: typeof import("@/lib/api/ats") = await vi.importActual(
    "@/lib/api/ats",
  );
  return {
    ...actual,
    listJobStatuses: (token: string, id: string) =>
      listJobStatusesMock(token, id),
    updateJobStatusFilter: (token: string, id: string, body: unknown) =>
      updateJobStatusFilterMock(token, id, body),
  };
});

function renderWithProviders(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("JobStatusFilterDialog", () => {
  beforeEach(() => {
    listJobStatusesMock.mockReset();
    updateJobStatusFilterMock.mockReset();
  });

  it("fetches statuses on open and preselects 'Active' when no prior filter", async () => {
    listJobStatusesMock.mockResolvedValue([
      { id: 1, name: "Active" },
      { id: 4, name: "Jobs Filled" },
    ]);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={null}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Active")).toBeChecked();
    });
    expect(screen.getByLabelText("Jobs Filled")).not.toBeChecked();
  });

  it("preselects prior filter ids on edit", async () => {
    listJobStatusesMock.mockResolvedValue([
      { id: 1, name: "Active" },
      { id: 4, name: "Jobs Filled" },
      { id: 8, name: "Reactivated" },
    ]);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={{ ids: [4, 8], names: ["Jobs Filled", "Reactivated"] }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Jobs Filled")).toBeChecked();
    });
    expect(screen.getByLabelText("Active")).not.toBeChecked();
    expect(screen.getByLabelText("Reactivated")).toBeChecked();
  });

  it("disables save when zero statuses are selected", async () => {
    listJobStatusesMock.mockResolvedValue([{ id: 1, name: "Active" }]);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={null}
      />,
    );

    await waitFor(() => screen.getByLabelText("Active"));
    fireEvent.click(screen.getByLabelText("Active")); // uncheck the autopick

    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("calls updateJobStatusFilter on save", async () => {
    listJobStatusesMock.mockResolvedValue([
      { id: 1, name: "Active" },
      { id: 8, name: "Reactivated" },
    ]);
    updateJobStatusFilterMock.mockResolvedValue(undefined);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={null}
      />,
    );

    await waitFor(() => screen.getByLabelText("Reactivated"));
    fireEvent.click(screen.getByLabelText("Reactivated"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(updateJobStatusFilterMock).toHaveBeenCalledWith("tok", "conn-1", {
        ids: [1, 8],
        names: ["Active", "Reactivated"],
      });
    });
  });
});
