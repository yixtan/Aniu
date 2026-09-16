import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listRunDays, listRuns, listSchedules } from "@/lib/api";
import type { RunSummary } from "@/lib/api-types";

import { RunsPage } from "./runs-page";

vi.mock("@/lib/api", () => ({
  deleteRun: vi.fn(),
  listRunDays: vi.fn(),
  listRuns: vi.fn(),
  listSchedules: vi.fn(),
}));

vi.mock("@/features/runs/components/run-start-button", () => ({
  RunStartButton: () => null,
}));

const workbench = vi.hoisted(() => vi.fn());
vi.mock("@/features/runs/components/run-workbench", () => ({
  RunWorkbenchPanel: (props: { runId: number | null }) => {
    workbench(props.runId);
    return <div data-testid="workbench">{props.runId ?? "none"}</div>;
  },
}));

/** 09:30 and 09:50 Beijing on 2026-09-14, written in UTC. */
const NINE_THIRTY = "2026-09-14T01:30:06Z";
const NINE_FIFTY = "2026-09-14T01:50:02Z";

function run(overrides: Partial<RunSummary> & { run_id: number }): RunSummary {
  return {
    task_id: overrides.run_id,
    trigger_source: "scheduled",
    schedule_id: 1,
    status: "COMPLETED",
    current_state: "Summary",
    summary: null,
    summary_render_mode: "markdown",
    started_at: NINE_THIRTY,
    completed_at: "2026-09-14T01:33:00Z",
    tool_calls_count: 0,
    thinking_count: 0,
    total_tokens: 0,
    cached_tokens: 0,
    trade_count: 0,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RunsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Noon Beijing on 2026-09-14, so the morning is past and the afternoon is not.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-14T04:00:00Z"));
  vi.mocked(listRunDays).mockResolvedValue([
    {
      day: "2026-09-14",
      analysis_total: 1,
      analysis_failed: 0,
      watch_total: 1,
      watch_failed: 0,
    },
    {
      day: "2026-09-11",
      analysis_total: 1,
      analysis_failed: 0,
      watch_total: 0,
      watch_failed: 0,
    },
  ]);
  vi.mocked(listSchedules).mockResolvedValue([
    {
      schedule_id: 1,
      enabled: true,
      task_type: "market_analysis",
      interval_minutes: 20,
      custom_schedule_times: null,
      schedule_times: ["09:30", "14:40"],
      revision: 1,
      runtime_synced_revision: 1,
      sync_error: null,
      updated_at: "2026-09-14T00:00:00Z",
    },
    {
      schedule_id: 2,
      enabled: true,
      task_type: "order_watch",
      interval_minutes: 3,
      custom_schedule_times: null,
      schedule_times: ["09:33", "14:57"],
      revision: 1,
      runtime_synced_revision: 1,
      sync_error: null,
      updated_at: "2026-09-14T00:00:00Z",
    },
  ]);
  vi.mocked(listRuns).mockResolvedValue([
    run({ run_id: 20260914101, started_at: NINE_THIRTY }),
    run({ run_id: 20260914301, started_at: "2026-09-14T01:33:08Z" }),
  ]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("RunsPage", () => {
  it("lays today out as the times each task was meant to run at", async () => {
    renderPage();

    const analysis = await screen.findByRole("region", { name: "操盘" });
    const watch = screen.getByRole("region", { name: "盯盘" });

    // A morning time that ran, and an afternoon one that has not come yet.
    expect(within(analysis).getByRole("button", { name: "09:30 已完成" })).toBeInTheDocument();
    expect(within(analysis).getByRole("button", { name: "14:40 未运行" })).toBeInTheDocument();
    expect(within(watch).getByRole("button", { name: "09:33 已完成" })).toBeInTheDocument();
  });

  it("keeps the two tasks in their own regions", async () => {
    renderPage();

    const analysis = await screen.findByRole("region", { name: "操盘" });

    // The watch's 09:33 must not be drawn among the analysis times.
    expect(within(analysis).queryByRole("button", { name: /09:33/ })).not.toBeInTheDocument();
  });

  it("shows the run of whichever time is clicked, from either task", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderPage();

    const watch = await screen.findByRole("region", { name: "盯盘" });
    await user.click(within(watch).getByRole("button", { name: "09:33 已完成" }));

    await waitFor(() =>
      expect(screen.getByTestId("workbench")).toHaveTextContent("20260914301"),
    );
  });

  it("refuses to select a time that produced no run", async () => {
    renderPage();

    const analysis = await screen.findByRole("region", { name: "操盘" });

    expect(within(analysis).getByRole("button", { name: "14:40 未运行" })).toBeDisabled();
  });

  it("draws a past day from its runs alone, with no planned times", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.mocked(listRuns).mockResolvedValue([
      run({ run_id: 20260911101, started_at: NINE_FIFTY }),
    ]);
    renderPage();

    await user.click(await screen.findByRole("button", { name: /查看 2026-09-11 的运行日程/ }));

    const analysis = await screen.findByRole("region", { name: "操盘" });
    await waitFor(() =>
      expect(within(analysis).getByRole("button", { name: /09:50 已完成/ })).toBeInTheDocument(),
    );
    // Today's timetable must not be painted onto a day that never ran it.
    expect(within(analysis).queryByRole("button", { name: /14:40/ })).not.toBeInTheDocument();
  });
});
