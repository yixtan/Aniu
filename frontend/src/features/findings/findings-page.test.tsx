import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FindingsPage } from "./findings-page";

const api = vi.hoisted(() => ({
  listOpenFindings: vi.fn(),
  closeOpenFinding: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FindingsPage />
    </QueryClientProvider>,
  );
}

function finding(overrides: Record<string, unknown> = {}) {
  return {
    finding_id: 7,
    evaluation_id: 1,
    finding: "五笔买入限价单全属 AI 硬件链，成交条件与风险条件相同",
    resolution_test: "说明在什么行情下它们会分批而非同时成交",
    status: "OPEN",
    times_disputed: 0,
    dispositions: [],
    created_at: "2026-09-17T08:00:00+00:00",
    closed_at: null,
    ...overrides,
  };
}

describe("FindingsPage", () => {
  it("explains what an empty list means rather than showing nothing", async () => {
    api.listOpenFindings.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("还没有未结议题")).toBeInTheDocument();
  });

  it("shows what would settle a finding, not only the finding", async () => {
    // An objection with no test is answered in every run and never leaves.
    api.listOpenFindings.mockResolvedValue([finding()]);

    renderPage();

    expect(await screen.findByText(/AI 硬件链/)).toBeInTheDocument();
    expect(screen.getByText(/分批而非同时成交/)).toBeInTheDocument();
  });

  it("makes being talked past as visible as being addressed", async () => {
    api.listOpenFindings.mockResolvedValue([
      finding({
        times_disputed: 3,
        dispositions: [
          {
            run_id: 20260917101,
            verdict: "DISAGREED",
            note: "经复核，该顾虑不成立",
            at: "2026-09-17T09:00:00+00:00",
          },
          {
            run_id: 20260917102,
            verdict: "ADJUSTED",
            note: "已拆到不相关主线",
            at: "2026-09-17T09:30:00+00:00",
          },
        ],
      }),
    ]);

    renderPage();

    expect(await screen.findByText("已被 2 次运行处置")).toBeInTheDocument();
    expect(screen.getByText("其中 3 次未做调整")).toBeInTheDocument();
    expect(screen.getByText(/不同意 · 经复核，该顾虑不成立/)).toBeInTheDocument();
    expect(screen.getByText(/已按此调整 · 已拆到不相关主线/)).toBeInTheDocument();
  });

  it("closes a finding when the person says so", async () => {
    api.listOpenFindings.mockResolvedValue([finding()]);
    api.closeOpenFinding.mockResolvedValue(finding({ status: "CLOSED" }));

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /关闭/ }));

    // react-query hands the mutation a context object as a second argument;
    // what matters is which finding was closed.
    await waitFor(() => expect(api.closeOpenFinding).toHaveBeenCalled());
    expect(api.closeOpenFinding.mock.calls[0]?.[0]).toBe(7);
  });

  it("keeps closed findings out of the list a run answers", async () => {
    api.listOpenFindings.mockResolvedValue([
      finding({ finding_id: 8, finding: "已经解决的那条", status: "CLOSED" }),
    ]);

    renderPage();

    const closed = await screen.findByText("已关闭");
    const section = closed.closest("section") as HTMLElement;
    expect(within(section).getByText("已经解决的那条")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /关闭/ })).not.toBeInTheDocument();
  });
});
