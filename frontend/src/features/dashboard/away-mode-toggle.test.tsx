import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AwayModeToggle } from "./away-mode-toggle";

const api = vi.hoisted(() => ({
  getAwayMode: vi.fn(),
  setAwayMode: vi.fn(),
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("sonner", () => ({ toast }));

const OFF = { enabled: false, active_date: null, updated_at: "2026-09-08T00:00:00Z" };
const ON = {
  enabled: true,
  active_date: "2026-09-08",
  updated_at: "2026-09-08T01:00:00Z",
};

function renderToggle() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AwayModeToggle />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

/** The switch stays disabled until its current state is known. */
async function readySwitch() {
  const toggle = await screen.findByRole("switch", { name: "离开模式" });
  await waitFor(() => expect(toggle).not.toBeDisabled());
  return toggle;
}

describe("AwayModeToggle", () => {
  it("reads as an off switch when away mode is not on", async () => {
    api.getAwayMode.mockResolvedValue(OFF);

    renderToggle();

    const toggle = await screen.findByRole("switch", { name: "离开模式" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveTextContent("离开模式");
  });

  it("reads as an on switch when away mode is on", async () => {
    api.getAwayMode.mockResolvedValue(ON);

    renderToggle();

    const toggle = await screen.findByRole("switch", { name: "离开模式" });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toggle).toHaveTextContent("离开中");
  });

  it("switches away mode on and says what it will do", async () => {
    const user = userEvent.setup();
    api.getAwayMode.mockResolvedValue(OFF);
    api.setAwayMode.mockResolvedValue(ON);

    renderToggle();

    await user.click(await readySwitch());

    await waitFor(() => expect(api.setAwayMode).toHaveBeenCalled());
    // react-query passes its own mutation context as a second argument.
    expect(api.setAwayMode.mock.calls[0]?.[0]).toBe(true);
    expect(toast.success).toHaveBeenCalledWith(
      "已开启离开模式：运行结束后自动发送报告邮件，午夜自动关闭",
    );
  });

  it("switches away mode back off", async () => {
    const user = userEvent.setup();
    api.getAwayMode.mockResolvedValue(ON);
    api.setAwayMode.mockResolvedValue(OFF);

    renderToggle();

    const toggle = await readySwitch();
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    await user.click(toggle);

    await waitFor(() => expect(api.setAwayMode).toHaveBeenCalled());
    // react-query passes its own mutation context as a second argument.
    expect(api.setAwayMode.mock.calls[0]?.[0]).toBe(false);
    expect(toast.success).toHaveBeenCalledWith("已关闭离开模式");
  });

  it("reports a rejected switch instead of pretending it worked", async () => {
    const user = userEvent.setup();
    api.getAwayMode.mockResolvedValue(OFF);
    api.setAwayMode.mockRejectedValue(new Error("服务不可用"));

    renderToggle();

    await user.click(await readySwitch());

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByRole("switch", { name: "离开模式" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });
});
