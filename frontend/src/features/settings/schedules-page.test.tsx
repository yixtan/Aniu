import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TradingSchedulesPage } from "./schedules-page";
import { ApiConflictError } from "@/lib/openapi-client";

const api = vi.hoisted(() => ({
  createSchedule: vi.fn(),
  listSchedules: vi.fn(),
  updateSchedule: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const schedule = {
  schedule_id: 20260731201,
  enabled: true,
  task_type: "market_analysis",
  interval_minutes: 30,
  custom_schedule_times: null,
  schedule_times: ["09:30", "10:00", "10:30", "11:00", "13:00", "13:30", "14:00", "14:30"],
  revision: 3,
  runtime_synced_revision: 3,
  sync_error: null,
  updated_at: "2026-07-31T08:00:00Z",
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TradingSchedulesPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("TradingSchedulesPage", () => {
  it("updates the interval with the visible revision and keeps sync status", async () => {
    const user = userEvent.setup();
    api.listSchedules.mockResolvedValue([schedule]);
    api.updateSchedule.mockResolvedValue({
      ...schedule,
      interval_minutes: 45,
      revision: 4,
      runtime_synced_revision: 4,
    });

    renderPage();

    expect(await screen.findByText("已同步")).toBeInTheDocument();
    const interval = screen.getByLabelText("操盘运行间隔（分钟）");
    await user.clear(interval);
    await user.type(interval, "45");
    await user.click(screen.getByRole("button", { name: "保存操盘间隔任务设置" }));

    await waitFor(() => expect(api.updateSchedule).toHaveBeenCalledTimes(1));
    expect(api.updateSchedule).toHaveBeenCalledWith(
      schedule.schedule_id,
      expect.objectContaining({
        enabled: true,
        expected_revision: 3,
        interval_minutes: 45,
        schedule_times: null,
        task_type: "market_analysis",
      }),
    );
  });

  it("keeps the conflict and local interval when reload fails", async () => {
    const user = userEvent.setup();
    api.listSchedules
      .mockResolvedValueOnce([schedule])
      .mockRejectedValueOnce(new Error("reload failed"));
    api.updateSchedule.mockRejectedValue(
      new ApiConflictError(
        "conflict",
        {},
        {
          resource: "strategy_schedule",
          expectedRevision: 3,
          actualRevision: 4,
          requestId: null,
        },
      ),
    );

    renderPage();

    const interval = await screen.findByLabelText("操盘运行间隔（分钟）");
    await user.clear(interval);
    await user.type(interval, "45");
    await user.click(screen.getByRole("button", { name: "保存操盘间隔任务设置" }));
    const reload = await screen.findByRole("button", { name: "重新加载服务端版本" });
    await user.click(reload);
    await waitFor(() => expect(api.listSchedules).toHaveBeenCalledTimes(2));

    expect(screen.getByLabelText("操盘运行间隔（分钟）")).toHaveValue(45);
    expect(screen.getByText("配置已被其他会话修改")).toBeInTheDocument();
  });

  it("expands the custom-time card on enable and submits the times", async () => {
    const user = userEvent.setup();
    api.listSchedules.mockResolvedValue([schedule]);
    api.updateSchedule.mockResolvedValue({
      ...schedule,
      custom_schedule_times: ["09:30"],
      schedule_times: ["09:30"],
      revision: 4,
      runtime_synced_revision: 4,
    });

    renderPage();

    expect(await screen.findByText("已同步")).toBeInTheDocument();
    const analysis = within(screen.getByRole("region", { name: "操盘任务" }));
    expect(analysis.queryByRole("button", { name: "添加" })).not.toBeInTheDocument();

    await user.click(analysis.getByRole("switch", { name: "启用操盘定时任务" }));

    await user.click(analysis.getByRole("button", { name: "添加" }));
    expect(analysis.getByText("09:30")).toBeInTheDocument();

    const minute = analysis.getByLabelText("分");
    await user.clear(minute);
    await user.type(minute, "45");
    await user.click(analysis.getByRole("button", { name: "添加" }));
    expect(analysis.getByText("09:45")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存操盘定时任务设置" }));

    await waitFor(() => expect(api.updateSchedule).toHaveBeenCalledTimes(1));
    expect(api.updateSchedule).toHaveBeenCalledWith(
      schedule.schedule_id,
      expect.objectContaining({
        enabled: true,
        expected_revision: 3,
        schedule_times: ["09:30", "09:45"],
        interval_minutes: 30,
      }),
    );
  });

  it("cannot save the custom-time card without adding any time", async () => {
    const user = userEvent.setup();
    api.listSchedules.mockResolvedValue([schedule]);

    renderPage();

    expect(await screen.findByText("已同步")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "启用操盘定时任务" }));

    expect(screen.getByText("请添加至少一个时点")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存操盘定时任务设置" })).toBeDisabled();
    expect(api.updateSchedule).not.toHaveBeenCalled();
  });

  it("disables the task when both modes are turned off", async () => {
    const user = userEvent.setup();
    api.listSchedules.mockResolvedValue([schedule]);
    api.updateSchedule.mockResolvedValue({
      ...schedule,
      enabled: false,
      revision: 4,
      runtime_synced_revision: 4,
    });

    renderPage();

    expect(await screen.findByText("已同步")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "停用操盘间隔任务" }));

    expect(screen.getByText("当前未启用任何运行方式，操盘任务将不会自动运行")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "停用操盘任务" }));

    await waitFor(() => expect(api.updateSchedule).toHaveBeenCalledTimes(1));
    expect(api.updateSchedule).toHaveBeenCalledWith(
      schedule.schedule_id,
      expect.objectContaining({
        enabled: false,
        expected_revision: 3,
        schedule_times: null,
      }),
    );
  });

  it("keeps custom times when disabling a custom-time schedule", async () => {
    const user = userEvent.setup();
    const customSchedule = {
      ...schedule,
      custom_schedule_times: ["09:30", "14:00"],
      schedule_times: ["09:30", "14:00"],
    };
    api.listSchedules.mockResolvedValue([customSchedule]);
    api.updateSchedule.mockResolvedValue({
      ...customSchedule,
      enabled: false,
      revision: 4,
      runtime_synced_revision: 4,
    });

    renderPage();

    expect(await screen.findByText("已同步")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "停用操盘定时任务" }));

    await user.click(screen.getByRole("button", { name: "停用操盘任务" }));

    await waitFor(() => expect(api.updateSchedule).toHaveBeenCalledTimes(1));
    expect(api.updateSchedule).toHaveBeenCalledWith(
      schedule.schedule_id,
      expect.objectContaining({
        enabled: false,
        expected_revision: 3,
        schedule_times: ["09:30", "14:00"],
      }),
    );
  });

  it("shows a disabled notice instead of a disable button when already off", async () => {
    api.listSchedules.mockResolvedValue([{ ...schedule, enabled: false }]);

    renderPage();

    expect(await screen.findByText("操盘任务已停用，不会自动运行")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "停用操盘任务" })).not.toBeInTheDocument();
    expect(api.updateSchedule).not.toHaveBeenCalled();
  });

  describe("order watch", () => {
    it("shows both kinds, with the watch unsaved and its gate warning visible", async () => {
      api.listSchedules.mockResolvedValue([schedule]);

      renderPage();

      expect(await screen.findByRole("region", { name: "操盘任务" })).toBeInTheDocument();
      const watch = within(screen.getByRole("region", { name: "盯盘任务" }));
      expect(watch.getByText(/启用后会立刻按此节奏运行/)).toBeInTheDocument();
      expect(watch.getByText(/为盯盘阶段选好模型/)).toBeInTheDocument();
      // The analysis row is saved and synced; the watch has no row yet.
      expect(screen.getAllByText("已同步")).toHaveLength(1);
    });

    it("creates a watch schedule with its own kind, floor and default cadence", async () => {
      const user = userEvent.setup();
      api.listSchedules.mockResolvedValue([schedule]);
      api.createSchedule.mockResolvedValue({
        ...schedule,
        schedule_id: 20260913202,
        task_type: "order_watch",
        interval_minutes: 3,
        revision: 1,
        runtime_synced_revision: 1,
      });

      renderPage();

      const watch = within(await screen.findByRole("region", { name: "盯盘任务" }));
      const interval = watch.getByLabelText("盯盘运行间隔（分钟）");
      expect(interval).toHaveValue(3);
      expect(interval).toHaveAttribute("min", "3");
      // Both sessions to the bell: 41 + 41 at three minutes.
      expect(watch.getByText("82 个")).toBeInTheDocument();
      expect(watch.getByText("11:30")).toBeInTheDocument();
      expect(watch.getByText("15:00")).toBeInTheDocument();

      await user.click(watch.getByRole("button", { name: "保存盯盘间隔任务设置" }));

      await waitFor(() => expect(api.createSchedule).toHaveBeenCalledTimes(1));
      expect(api.createSchedule).toHaveBeenCalledWith({
        enabled: true,
        task_type: "order_watch",
        interval_minutes: 3,
        schedule_times: null,
      });
      expect(api.updateSchedule).not.toHaveBeenCalled();
    });

    it("keeps the analysis floor at fifteen and its preview short of the bell", async () => {
      api.listSchedules.mockResolvedValue([schedule]);

      renderPage();

      const analysis = within(await screen.findByRole("region", { name: "操盘任务" }));
      expect(analysis.getByLabelText("操盘运行间隔（分钟）")).toHaveAttribute("min", "15");
      expect(analysis.queryByText("15:00")).not.toBeInTheDocument();
    });

    it("disables the watch without touching the analysis schedule", async () => {
      const user = userEvent.setup();
      const watchSchedule = {
        ...schedule,
        schedule_id: 20260913202,
        task_type: "order_watch",
        interval_minutes: 3,
      };
      api.listSchedules.mockResolvedValue([schedule, watchSchedule]);
      api.updateSchedule.mockResolvedValue({ ...watchSchedule, enabled: false, revision: 4 });

      renderPage();

      const watch = within(await screen.findByRole("region", { name: "盯盘任务" }));
      await user.click(watch.getByRole("switch", { name: "停用盯盘间隔任务" }));
      await user.click(watch.getByRole("button", { name: "停用盯盘任务" }));

      await waitFor(() => expect(api.updateSchedule).toHaveBeenCalledTimes(1));
      expect(api.updateSchedule).toHaveBeenCalledWith(
        watchSchedule.schedule_id,
        expect.objectContaining({ enabled: false, task_type: "order_watch" }),
      );
    });
  });
});
