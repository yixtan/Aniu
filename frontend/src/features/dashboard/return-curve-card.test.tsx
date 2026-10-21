import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReturnCurveData } from "./return-curve";
import { ReturnCurveCard } from "./return-curve-card";

const api = vi.hoisted(() => ({ getReturnCurve: vi.fn() }));
vi.mock("@/lib/api", () => api);

function day(
  date: string,
  totalAsset: number,
  benchmarkClose: number | null,
  extra: Partial<ReturnCurveData["days"][number]> = {},
): ReturnCurveData["days"][number] {
  return {
    date,
    total_asset: totalAsset,
    market_value: 50_000,
    source: "recorded",
    is_close: true,
    benchmark_close: benchmarkClose,
    fills: [],
    ...extra,
  };
}

const curve: ReturnCurveData = {
  benchmark_name: "沪深300",
  initial_capital: 1_000_000,
  open_date: "2026-08-28",
  base_date: "2026-08-27",
  base_benchmark_close: 4000,
  days: [
    day("2026-08-28", 1_000_000, 4040, { source: "reconstructed" }),
    day("2026-09-30", 1_020_000, 4200, {
      source: "reconstructed",
      fills: [
        {
          symbol: "600276",
          stock_name: "恒瑞医药",
          direction: "BUY",
          quantity: 400,
          price: 45.95,
        },
      ],
    }),
    day("2026-10-08", 990_000, 4100),
    day("2026-10-21", 1_010_000, null, { is_close: false }),
  ],
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReturnCurveCard />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("ReturnCurveCard", () => {
  it("shows the range's return beside the benchmark's", async () => {
    api.getReturnCurve.mockResolvedValueOnce(curve);

    renderCard();

    expect(await screen.findByText("累计收益")).toBeInTheDocument();
    expect(screen.getByText("+1.00%")).toBeInTheDocument();
    // The last day's index close is not settled yet; the latest one is 10/8.
    expect(screen.getByText("同期沪深300")).toBeInTheDocument();
    expect(screen.getByText("+2.50%")).toBeInTheDocument();
    expect(screen.getByText("-2.94%")).toBeInTheDocument();
  });

  it("re-measures from the close before a shorter range", async () => {
    api.getReturnCurve.mockResolvedValueOnce(curve);
    const user = userEvent.setup();

    renderCard();
    await screen.findByText("累计收益");
    await user.click(screen.getByRole("button", { name: "近 1 月" }));

    expect(screen.getByRole("button", { name: "近 1 月" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("9/30 起")).toBeInTheDocument();
  });

  it("explains a flat line by a low position, and marks estimated and intraday points", async () => {
    api.getReturnCurve.mockResolvedValueOnce(curve);

    renderCard();

    expect(await screen.findByText(/大部分资金是现金/)).toBeInTheDocument();
    expect(screen.getByText(/事后用成交记录推算的/)).toBeInTheDocument();
    expect(screen.getByText("最后一个点是盘中数值，收盘后会更新。")).toBeInTheDocument();
  });

  it("lets the keyboard step through days and shows that day's fills", async () => {
    api.getReturnCurve.mockResolvedValueOnce(curve);

    renderCard();
    const chart = await screen.findByRole("img", { name: /累计收益曲线/ });
    chart.focus();
    // The first step lands one day before the latest; the second on 9/30.
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    fireEvent.keyDown(chart, { key: "ArrowLeft" });

    const tooltip = screen.getByRole("status");
    expect(tooltip).toHaveTextContent("2026-09-30（推算）");
    expect(tooltip).toHaveTextContent("买入 恒瑞医药 400 股");
  });

  it("says plainly when nothing has been recorded yet", async () => {
    api.getReturnCurve.mockResolvedValueOnce({ ...curve, days: [] });

    renderCard();

    expect(await screen.findByText(/还没有记录/)).toBeInTheDocument();
  });

  it("offers a retry when the curve cannot load", async () => {
    api.getReturnCurve.mockRejectedValueOnce(new Error("网络错误"));

    renderCard();

    expect(await screen.findByText(/收益曲线加载失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
