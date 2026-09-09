import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { formatTokens } from "@/lib/format";

import { SystemStatusPanel } from "./system-status-panel";

const api = vi.hoisted(() => ({
  getSystemStatus: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

/** "2026-09-09" minus `offset` days, as the API spells a day. */
function day(offset: number) {
  const date = new Date(2026, 8, 9);
  date.setDate(date.getDate() - offset);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const dayOfMonth = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${dayOfMonth}`;
}

function dailyRow(offset: number, overrides: Record<string, number> = {}) {
  return {
    day: day(offset),
    runs_completed: 0,
    runs_failed: 0,
    summaries_html: 0,
    tokens: 0,
    trades_completed: 0,
    trades_failed: 0,
    memory_writes: 0,
    memory_write_failures: 0,
    memory_reads: 0,
    memory_distinct_queries: 0,
    data_calls: 0,
    data_call_failures: 0,
    ...overrides,
  };
}

const status = {
  generated_at: "2026-09-09T12:00:00+00:00",
  days: [
    dailyRow(0, {
      runs_completed: 16,
      summaries_html: 16,
      tokens: 780_000,
      data_calls: 134,
      memory_reads: 19,
      memory_distinct_queries: 5,
    }),
    dailyRow(1, {
      runs_completed: 16,
      runs_failed: 1,
      summaries_html: 15,
      tokens: 1_080_000,
      trades_completed: 3,
      data_calls: 130,
      data_call_failures: 11,
      memory_writes: 11,
      memory_write_failures: 25,
      memory_reads: 23,
      memory_distinct_queries: 14,
    }),
    ...[2, 3, 4, 5, 6].map((offset) => dailyRow(offset)),
  ],
  tokens: Array.from({ length: 30 }, (_, offset) => ({
    day: day(offset),
    tokens: offset === 0 ? 780_000 : offset === 1 ? 1_080_000 : 0,
    runs: offset < 2 ? 16 : 0,
  })),
  dreams: [
    {
      target_date: "2026-09-08",
      status: "completed",
      completed_at: "2026-09-08T15:32:00+00:00",
      failure_reason: null,
      created: 3,
      updated: 2,
      deleted: 11,
    },
    {
      target_date: "2026-09-07",
      status: "failed",
      completed_at: "2026-09-07T15:40:00+00:00",
      failure_reason: "memory_write 工具的内部 bug",
      created: 0,
      updated: 0,
      deleted: 0,
    },
  ],
  memory_live: 53,
  memory_deleted: 30,
  memory_with_lineage: 0,
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SystemStatusPanel />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("formatTokens", () => {
  it("reads tokens at the scale they cost", () => {
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(49_000)).toBe("49k");
    expect(formatTokens(1_080_000)).toBe("1.08M");
  });
});

describe("SystemStatusPanel", () => {
  it("shows today's numbers and marks the failures in the week", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const today = await screen.findByRole("region", { name: "今日状态" });
    const runs = within(today).getByText("运行").closest("[data-slot=card]");
    expect(runs).not.toBeNull();
    expect(within(runs as HTMLElement).getByText("16 / 16")).toBeInTheDocument();
    expect(within(today).getByText("134")).toBeInTheDocument();

    const week = screen.getByRole("region", { name: "近 7 天" });
    const rows = within(week).getAllByRole("row");
    // One header row, then a fixed seven days newest first.
    expect(rows).toHaveLength(8);
    expect(rows[1]).toHaveTextContent("09-09 周三");

    const yesterday = rows[2] as HTMLElement;
    expect(yesterday).toHaveTextContent("09-08 周二");
    expect(within(yesterday).getByText("25")).toHaveClass("text-destructive");
    expect(within(yesterday).getByText("15")).toHaveClass("text-destructive");
    // A zero that is not a failure is not shouted about.
    for (const zero of within(rows[1] as HTMLElement).getAllByText("0")) {
      expect(zero).not.toHaveClass("text-destructive");
    }
  });

  it("sums a month of tokens and reads them per day and per run", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const chart = await screen.findByText("Token 消耗（近 30 天）");
    const card = chart.closest("[data-slot=card]") as HTMLElement;
    expect(within(card).getByText("1.86M")).toBeInTheDocument();
    // 1.86M over the two days that ran: under a million, so it reads in k.
    expect(within(card).getByText("930k")).toBeInTheDocument();
    expect(within(card).getByText("58k")).toBeInTheDocument();
    expect(within(card).getAllByRole("listitem")).toHaveLength(30);
    // With nothing under the pointer the readout names today.
    expect(within(card).getByText("09-09 周三 · 780k · 16 次运行")).toBeInTheDocument();
  });

  it("lists the recent dreams and what each did to memory", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const title = await screen.findByText("记忆梦境（最近 2 次）");
    const card = title.closest("[data-slot=card]") as HTMLElement;
    expect(within(card).getByText("53 条")).toBeInTheDocument();
    expect(within(card).getByText("30 条")).toBeInTheDocument();

    const rows = within(card).getAllByRole("row");
    expect(rows).toHaveLength(3);
    const latest = rows[1] as HTMLElement;
    expect(latest).toHaveTextContent("09-08 周二");
    expect(within(latest).getByText("已完成")).toBeInTheDocument();
    expect(latest).toHaveTextContent(/3.*2.*11/);
    const failed = rows[2] as HTMLElement;
    expect(within(failed).getByText("失败")).toBeInTheDocument();
    expect(within(failed).getByText("memory_write 工具的内部 bug")).toHaveClass("text-destructive");
  });

  it("says so when the status cannot be loaded", async () => {
    api.getSystemStatus.mockRejectedValue(new Error("boom"));

    renderPanel();

    expect(await screen.findByText("系统状态加载失败")).toBeInTheDocument();
  });
});
