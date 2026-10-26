import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { ExecutionCheckPanel } from "./execution-check";
const api = vi.hoisted(() => ({ getExecutionCheck: vi.fn(), refreshAccountCache: vi.fn() }));
vi.mock("@/lib/api", () => api);
it("shows interrupted write evidence and refreshes without rerunning trades", async () => {
  api.getExecutionCheck.mockResolvedValue({
    interrupted: true,
    unfinished_steps: ["报告未生成"],
    evidence_stale: true,
    account_observed_at: null,
    notice: "不重新下单",
    items: [
      {
        tool_call_id: "one",
        instruction: "买入合成股票",
        outcome: "accepted",
        order_id: "test",
        order_status: "PENDING",
        filled_quantity: 0,
        explanation: "受理不代表成交",
      },
    ],
  });
  api.refreshAccountCache.mockResolvedValue({ status: "stale" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ExecutionCheckPanel runId={1} status="FAILED" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/运行中断不代表交易未发生/)).toBeInTheDocument();
  expect(screen.getByText(/已受理，未确认成交/)).toBeInTheDocument();
  expect(screen.getByText(/报告未生成/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "刷新账户并核对" }));
  await waitFor(() => expect(api.getExecutionCheck).toHaveBeenCalledTimes(2));
  expect(api.refreshAccountCache).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("账户刷新未成功，仍显示上次观测结果。")).toBeInTheDocument();
});
