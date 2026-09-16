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
    watches_completed: 0,
    watches_failed: 0,
    watch_tokens: 0,
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
      // A full day of watches: eighty-two passes, one of them failed, and
      // more tokens than the analysis runs — which is exactly why they are
      // folded apart rather than into the columns above.
      watches_completed: 81,
      watches_failed: 1,
      watch_tokens: 2_000_000,
      cached_tokens: 2_400_000,
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
    // Today's watches moved to DeepSeek while the analyses stayed on v2ex,
    // which is the whole reason the bars are split by provider.
    channels:
      offset === 0
        ? [
            { channel_id: 5, name: "DeepSeek", tokens: 2_000_000 },
            { channel_id: 2, name: "v2ex", tokens: 780_000 },
          ]
        : offset === 1
          ? // The dream of that night counts inside its provider, not beside
            // it: 1.08M of runs plus the 442k the dream spent.
            [{ channel_id: 2, name: "v2ex", tokens: 1_522_124 }]
          : [],
    dream_tokens: offset === 1 ? 442_124 : 0,
    watch_tokens: offset === 0 ? 2_000_000 : 0,
    watches: offset === 0 ? 82 : 0,
    // Inside the three above, never added to them.
    cached_tokens: offset === 0 ? 2_400_000 : offset === 1 ? 380_000 : 0,
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
      total_tokens: 442_124,
      cached_tokens: 380_000,
    },
    {
      target_date: "2026-09-07",
      status: "failed",
      completed_at: "2026-09-07T15:40:00+00:00",
      failure_reason: "memory_write 工具的内部 bug",
      created: 0,
      updated: 0,
      deleted: 0,
      total_tokens: 0,
      cached_tokens: 0,
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
    // 1.86M of runs, 2.00M of watches, and the 442k a dream spent reading
    // the day back — 4.30M in all, each tier shown on its own.
    expect(within(card).getByText("4.30M")).toBeInTheDocument();
    // 2.00M appears twice now: once as the watch stat, once as DeepSeek's
    // share in the legend. They are the same tokens counted two ways.
    expect(within(card).getAllByText("2.00M")).toHaveLength(2);
    // Only the 其中梦境 stat now — the dream's tokens are inside v2ex's band.
    expect(within(card).getByText("442k")).toBeInTheDocument();
    // 日均 over the two active days.
    expect(within(card).getByText("2.15M")).toBeInTheDocument();
    // 每次运行 stays an analysis figure: watches do not dilute it.
    expect(within(card).getByText("72k")).toBeInTheDocument();
    // 每次盯盘: 2.00M over 82 passes.
    expect(within(card).getByText("24k")).toBeInTheDocument();
    const bars = within(card).getByRole("list", { name: /每日 Token 消耗/ });
    expect(within(bars).getAllByRole("listitem")).toHaveLength(30);
    // With nothing under the pointer the readout names today — the task split
    // it used to carry alone, now followed by who was asked. In the legend's
    // order, not the day's own: v2ex leads the window even though DeepSeek
    // spent more on this particular day.
    expect(
      within(card).getByText(
        "09-09 周三 · 780k · 16 次运行，盯盘 2.00M · 82 次，其中缓存 2.40M，v2ex 780k · DeepSeek 2.00M",
      ),
    ).toBeInTheDocument();
  });

  it("names every provider in the window, heaviest first", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const chart = await screen.findByText("Token 消耗（近 30 天）");
    const card = chart.closest("[data-slot=card]") as HTMLElement;
    const legend = within(card).getByRole("list", { name: "渠道" });

    // Ordered by total spend, so a provider keeps its colour and its place
    // between visits. Dreams are inside these totals, not beside them.
    expect(
      within(legend)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["v2ex2.30M", "DeepSeek2.00M"]);
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

  it("shows what a dream cost, on the day it reflected on", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const chart = await screen.findByText("Token 消耗（近 30 天）");
    const card = chart.closest("[data-slot=card]") as HTMLElement;
    // The dream ran on the night of the 8th and is filed under the 8th.
    const bars = within(card).getAllByRole("listitem");
    const eighth = bars.find((bar) => bar.getAttribute("aria-label")?.startsWith("2026-09-08"));
    expect(eighth?.getAttribute("aria-label")).toContain("梦境 442,124 tokens");

    const dreamsTitle = screen.getByText("记忆梦境（最近 2 次）");
    const dreamsCard = dreamsTitle.closest("[data-slot=card]") as HTMLElement;
    const latest = within(dreamsCard).getAllByRole("row")[1] as HTMLElement;
    expect(latest).toHaveTextContent("442k");
    // A dream whose endpoint reported nothing shows a dash, not a zero.
    const failed = within(dreamsCard).getAllByRole("row")[2] as HTMLElement;
    expect(failed).toHaveTextContent("--");
  });

  it("reads the cached share apart from what it sits inside", async () => {
    // A tool loop resends the same prefix every turn, and a provider bills
    // that hit at a fraction of fresh input. Reading only the total made the
    // 2026-09-16 dream look eleven times more expensive than the night
    // before, when 86% of it had been served from cache.
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const chart = await screen.findByText("Token 消耗（近 30 天）");
    const card = chart.closest("[data-slot=card]") as HTMLElement;
    const stats = within(card).getByText("其中缓存命中").closest("div") as HTMLElement;
    expect(stats).toHaveTextContent("2.78M");

    // The day's own figure, on the bar and in the readout.
    const bars = within(card).getAllByRole("listitem");
    const today = bars[bars.length - 1] as HTMLElement;
    expect(today.getAttribute("aria-label")).toContain("其中缓存命中 2,400,000 tokens");

    // And on the dream that spent it.
    const dreamsTitle = screen.getByText("记忆梦境（最近 2 次）");
    const dreamsCard = dreamsTitle.closest("[data-slot=card]") as HTMLElement;
    const latest = within(dreamsCard).getAllByRole("row")[1] as HTMLElement;
    expect(latest).toHaveTextContent("442k");
    expect(latest).toHaveTextContent("缓存 380k");
    // A dream that reported none says nothing rather than claiming zero.
    const failed = within(dreamsCard).getAllByRole("row")[2] as HTMLElement;
    expect(failed).not.toHaveTextContent("缓存");
  });

  it("says so when the status cannot be loaded", async () => {
    api.getSystemStatus.mockRejectedValue(new Error("boom"));

    renderPanel();

    expect(await screen.findByText("系统状态加载失败")).toBeInTheDocument();
  });

  it("folds the watch apart from the runs everywhere it is shown", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    // Today's run card: the analysis count is untouched by eighty-two watches.
    const today = await screen.findByRole("region", { name: "今日状态" });
    expect(within(today).getByText("16")).toBeInTheDocument();
    expect(within(today).getByText("81")).toBeInTheDocument();
    const watchFailed = within(today).getByText("1", { selector: ".text-destructive" });
    expect(watchFailed).toBeInTheDocument();

    // The 7-day table has its own watch columns; the failure alarms.
    const table = screen.getByRole("region", { name: "近 7 天" });
    expect(within(table).getByRole("columnheader", { name: "盯盘" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "盯盘失败" })).toBeInTheDocument();
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByText("81")).toBeInTheDocument();

    // The chart's bar for today carries the watch tokens by name.
    const chart = screen.getByText("Token 消耗（近 30 天）");
    const card = chart.closest("[data-slot=card]") as HTMLElement;
    const bars = within(card).getAllByRole("listitem");
    const todayBar = bars[bars.length - 1] as HTMLElement;
    expect(todayBar.getAttribute("aria-label")).toContain("盯盘 2,000,000 tokens");
    expect(todayBar.getAttribute("aria-label")).toContain("82 次盯盘");
  });
});

describe("TokenChart stacking", () => {
  it("stacks every bar in the legend's order, not the day's own", async () => {
    api.getSystemStatus.mockResolvedValue(status);

    renderPanel();

    const chart = await screen.findByText("Token 消耗（近 30 天）");
    const card = chart.closest("[data-slot=card]") as HTMLElement;
    const bars = within(card).getByRole("list", { name: /每日 Token 消耗/ });
    const today = within(bars).getAllByRole("listitem").at(-1) as HTMLElement;

    // DeepSeek spent more than v2ex on this day, but v2ex leads the window, so
    // v2ex stays at the bottom. A column's first child is its top one, so the
    // legend's first provider is the last element here.
    expect(
      Array.from(today.querySelectorAll("[data-channel]"), (band) =>
        band.getAttribute("data-channel"),
      ),
    ).toEqual(["DeepSeek", "v2ex"]);
  });
});
