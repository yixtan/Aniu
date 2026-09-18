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
    settlement_proposed: false,
    dispositions: [],
    created_at: "2026-09-17T08:00:00+00:00",
    closed_at: null,
    closing_note: "",
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

  it("will not close a finding until it is told why", async () => {
    // Without the sentence, "settled by evidence" and "dropped, wrong
    // question" are the same row a month later.
    api.listOpenFindings.mockResolvedValue([finding()]);

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /^关闭/ }));

    expect(screen.getByRole("button", { name: "确认关闭" })).toBeDisabled();
    expect(api.closeOpenFinding).not.toHaveBeenCalled();
  });

  it("closes a finding with the reason the person gave", async () => {
    api.listOpenFindings.mockResolvedValue([finding()]);
    api.closeOpenFinding.mockResolvedValue(finding({ status: "CLOSED" }));

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /^关闭/ }));
    await userEvent.type(
      screen.getByLabelText("了结说明（必填）"),
      "浅档三笔分批成交，条件由行情满足。",
    );
    await userEvent.click(screen.getByRole("button", { name: "确认关闭" }));

    // react-query hands the mutation a context object as a second argument;
    // what matters is which finding was closed, and why.
    await waitFor(() => expect(api.closeOpenFinding).toHaveBeenCalled());
    expect(api.closeOpenFinding.mock.calls[0]?.[0]).toEqual({
      findingId: 7,
      note: "浅档三笔分批成交，条件由行情满足。",
    });
  });

  it("says which findings are waiting on the operator", async () => {
    // A run may say the test is met; it cannot act on that. The badge is how
    // the person learns there is something to decide.
    api.listOpenFindings.mockResolvedValue([
      finding({
        settlement_proposed: true,
        dispositions: [
          {
            run_id: 20260918105,
            verdict: "SETTLED",
            note: "浅档三笔成交、深档未触发。",
            at: "2026-09-18T02:52:26+00:00",
          },
        ],
      }),
    ]);

    renderPage();

    expect(await screen.findByText(/运行认为可了结/)).toBeInTheDocument();
    expect(screen.getByText("最近：认为可了结")).toBeInTheDocument();
  });

  it("shows the newest verdict without expanding four paragraphs", async () => {
    api.listOpenFindings.mockResolvedValue([
      finding({
        dispositions: [
          { run_id: 1, verdict: "UNDECIDED", note: "还看不出来。", at: "2026-09-18T01:00:00+00:00" },
          { run_id: 2, verdict: "ADJUSTED", note: "已拆到不相关主线。", at: "2026-09-18T02:00:00+00:00" },
        ],
      }),
    ]);

    renderPage();

    expect(await screen.findByText("最近：已按此调整")).toBeInTheDocument();
  });

  it("says why a closed finding closed", async () => {
    api.listOpenFindings.mockResolvedValue([
      finding({
        status: "CLOSED",
        closing_note: "浅档三笔分批成交，条件由行情满足。",
      }),
    ]);

    renderPage();

    expect(await screen.findByText(/浅档三笔分批成交/)).toBeInTheDocument();
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
