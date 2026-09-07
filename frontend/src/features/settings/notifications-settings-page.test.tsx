import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsSettingsPage } from "./notifications-settings-page";

const api = vi.hoisted(() => ({
  listNotificationChannels: vi.fn(),
  listNotificationDeliveries: vi.fn(),
  createNotificationChannel: vi.fn(),
  updateNotificationChannel: vi.fn(),
  deleteNotificationChannel: vi.fn(),
  testNotificationChannel: vi.fn(),
}));

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("sonner", () => ({ toast }));

const channel = {
  id: 1,
  name: "我的手机",
  kind: "webhook",
  enabled: true,
  subscribed_events: ["order_placed", "order_filled"],
  body_template: null,
  target_hint: "https://hook.test/…1234",
  created_at: "2026-09-07T08:00:00Z",
  updated_at: "2026-09-07T08:00:00Z",
} as const;

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationsSettingsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.listNotificationDeliveries.mockResolvedValue({ items: [], total: 0 });
});

afterEach(() => vi.clearAllMocks());

describe("NotificationsSettingsPage", () => {
  it("prompts to add a channel when none exist", async () => {
    api.listNotificationChannels.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("还没有推送通道")).toBeInTheDocument();
  });

  it("shows the masked target and subscribed events, never a secret", async () => {
    api.listNotificationChannels.mockResolvedValue([channel]);

    renderPage();

    expect(await screen.findByText("我的手机")).toBeInTheDocument();
    expect(screen.getByText("https://hook.test/…1234")).toBeInTheDocument();
    expect(screen.getAllByText("下单").length).toBeGreaterThan(0);
    expect(screen.queryByText(/abcd1234/)).not.toBeInTheDocument();
  });

  it("creates a webhook channel from the dialog", async () => {
    const user = userEvent.setup();
    api.listNotificationChannels.mockResolvedValue([]);
    api.createNotificationChannel.mockResolvedValue(channel);

    renderPage();

    await user.click(await screen.findByRole("button", { name: /新增通道/ }));
    await user.type(screen.getByLabelText("通道名称"), "我的手机");
    await user.type(screen.getByLabelText("地址 / 密钥"), "https://hook.test/abcd1234");
    await user.click(screen.getByRole("button", { name: "创建通道" }));

    await waitFor(() => expect(api.createNotificationChannel).toHaveBeenCalledTimes(1));
    // react-query passes its own mutation context as a second argument.
    expect(api.createNotificationChannel.mock.calls[0]?.[0]).toEqual({
      name: "我的手机",
      kind: "webhook",
      secret: "https://hook.test/abcd1234",
      enabled: true,
      subscribed_events: ["order_placed", "order_cancelled", "order_filled", "run_failed"],
      body_template: null,
    });
  });

  it("reports a failed test delivery as an error", async () => {
    const user = userEvent.setup();
    api.listNotificationChannels.mockResolvedValue([channel]);
    api.testNotificationChannel.mockResolvedValue({
      channel_id: 1,
      delivered: false,
      message: "发送失败：HTTP 500",
    });

    renderPage();

    await user.click(await screen.findByRole("button", { name: /测试/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("发送失败：HTTP 500"));
  });

  it("refuses to clear the last subscribed event", async () => {
    const user = userEvent.setup();
    api.listNotificationChannels.mockResolvedValue([
      { ...channel, subscribed_events: ["order_placed"] },
    ]);

    renderPage();

    const checkbox = await screen.findByRole("checkbox", { name: /下单/ });
    await user.click(checkbox);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("至少需要保留一个推送事件"));
    expect(api.updateNotificationChannel).not.toHaveBeenCalled();
  });

  it("toggles a channel off through the enable switch", async () => {
    const user = userEvent.setup();
    api.listNotificationChannels.mockResolvedValue([channel]);
    api.updateNotificationChannel.mockResolvedValue({ ...channel, enabled: false });

    renderPage();

    await user.click(await screen.findByRole("switch", { name: "启用通道 我的手机" }));

    await waitFor(() =>
      expect(api.updateNotificationChannel).toHaveBeenCalledWith(1, { enabled: false }),
    );
  });
  it("offers run failure as a subscribable event", async () => {
    const user = userEvent.setup();
    api.listNotificationChannels.mockResolvedValue([]);

    renderPage();

    await user.click(await screen.findByRole("button", { name: /新增通道/ }));

    expect(screen.getByRole("checkbox", { name: /运行失败/ })).toBeChecked();
    expect(screen.getByText("任务运行以失败告终；手动中止不会推送")).toBeInTheDocument();
  });

  it("subscribes a new channel to run failures by default", async () => {
    const user = userEvent.setup();
    api.listNotificationChannels.mockResolvedValue([]);
    api.createNotificationChannel.mockResolvedValue(channel);

    renderPage();

    await user.click(await screen.findByRole("button", { name: /新增通道/ }));
    await user.type(screen.getByLabelText("通道名称"), "手机");
    await user.type(screen.getByLabelText("地址 / 密钥"), "https://hook.test/x");
    await user.click(screen.getByRole("button", { name: "创建通道" }));

    await waitFor(() => expect(api.createNotificationChannel).toHaveBeenCalledTimes(1));
    expect(api.createNotificationChannel.mock.calls[0]?.[0].subscribed_events).toContain(
      "run_failed",
    );
  });

  it("shows delivery history with the failure detail", async () => {
    api.listNotificationChannels.mockResolvedValue([channel]);
    api.listNotificationDeliveries.mockResolvedValue({
      total: 2,
      items: [
        {
          id: 2,
          channel_id: 1,
          channel_name: "我的手机",
          channel_kind: "serverchan",
          event_kind: "run_failed",
          event_label: "运行失败",
          title: "Aniu 运行失败 · 运行 #128",
          status: "failed",
          error_message: "推送失败：HTTP 500",
          is_test: false,
          created_at: "2026-09-07T10:00:00Z",
        },
      ],
    });

    renderPage();

    expect(await screen.findByText("Aniu 运行失败 · 运行 #128")).toBeInTheDocument();
    expect(screen.getByText("推送失败：HTTP 500")).toBeInTheDocument();
    expect(screen.getByText(/我的手机 · 运行失败/)).toBeInTheDocument();
  });

  it("explains an empty history instead of showing a blank area", async () => {
    api.listNotificationChannels.mockResolvedValue([channel]);

    renderPage();

    expect(await screen.findByText(/还没有推送记录/)).toBeInTheDocument();
  });
});
