import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WatchlistPage } from "./watchlist-page";

const api = vi.hoisted(() => ({
  listWatchlist: vi.fn(),
  addWatchlistItem: vi.fn(),
  deleteWatchlistItem: vi.fn(),
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("sonner", () => ({ toast }));

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

function item(id: number, symbol: string, name: string, days: number) {
  return {
    id,
    symbol,
    name,
    created_at: daysAgo(days),
    updated_at: daysAgo(days),
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WatchlistPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("WatchlistPage", () => {
  it("explains what the list is for when it is empty", async () => {
    api.listWatchlist.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("还没有关注的股票")).toBeInTheDocument();
  });

  it("adds a company from its code alone", async () => {
    const user = userEvent.setup();
    api.listWatchlist.mockResolvedValue([]);
    api.addWatchlistItem.mockResolvedValue(item(1, "600519.SH", "贵州茅台", 0));

    renderPage();

    await user.type(await screen.findByLabelText("股票代码"), "600519");
    await user.click(screen.getByRole("button", { name: /加入关注/ }));

    // react-query passes its own mutation context as a second argument.
    await waitFor(() => expect(api.addWatchlistItem).toHaveBeenCalledTimes(1));
    expect(api.addWatchlistItem.mock.calls[0]?.[0]).toBe("600519");
  });

  it("shows how long each company has been followed", async () => {
    api.listWatchlist.mockResolvedValue([
      item(1, "600519.SH", "贵州茅台", 0),
      item(2, "000001.SZ", "平安银行", 5),
      item(3, "300750.SZ", "宁德时代", 70),
    ]);

    renderPage();

    expect(await screen.findByText("今天")).toBeInTheDocument();
    expect(screen.getByText("5 天")).toBeInTheDocument();
    expect(screen.getByText("2 个月")).toBeInTheDocument();
  });

  it("refuses to add an eleventh company", async () => {
    const full = Array.from({ length: 10 }, (_, index) =>
      item(index + 1, `60000${index}.SH`, `公司${index}`, index),
    );
    api.listWatchlist.mockResolvedValue(full);

    renderPage();

    expect(await screen.findByLabelText("股票代码")).toBeDisabled();
    expect(screen.getByText(/已达上限 10 只/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /加入关注/ })).toBeDisabled();
  });

  it("removes a company from the list", async () => {
    const user = userEvent.setup();
    api.listWatchlist.mockResolvedValue([item(7, "600519.SH", "贵州茅台", 3)]);
    api.deleteWatchlistItem.mockResolvedValue(undefined);

    renderPage();

    await user.click(await screen.findByRole("button", { name: "移出关注 贵州茅台" }));

    await waitFor(() => expect(api.deleteWatchlistItem).toHaveBeenCalledTimes(1));
    expect(api.deleteWatchlistItem.mock.calls[0]?.[0]).toBe(7);
  });

  it("reports why an addition was refused", async () => {
    const user = userEvent.setup();
    api.listWatchlist.mockResolvedValue([]);
    api.addWatchlistItem.mockRejectedValue(new Error("600519.SH 已在关注清单中"));

    renderPage();

    await user.type(await screen.findByLabelText("股票代码"), "600519");
    await user.click(screen.getByRole("button", { name: /加入关注/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("600519.SH 已在关注清单中"));
  });
});
