import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReturnCurveData } from "./return-curve";
import { ReturnCurveCard } from "./return-curve-card";
import { accountKeys } from "./query-keys";

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

function renderCard(cached?: ReturnCurveData) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (cached) client.setQueryData(accountKeys.returnCurve(), cached);
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
    expect(screen.getByText("截至 10/8 收盘")).toBeInTheDocument();
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
    expect(
      screen.getByText("最后一个点尚未记录到收盘，显示的是当天最后一次记录。"),
    ).toBeInTheDocument();
    expect(screen.getByText(/部分日期缺少指数收盘数据/)).toBeInTheDocument();
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

  it("explains a missing endpoint and retries successfully", async () => {
    api.getReturnCurve
      .mockRejectedValueOnce(Object.assign(new Error("Not Found"), { status: 404 }))
      .mockResolvedValueOnce(curve);
    const user = userEvent.setup();
    renderCard();

    expect(await screen.findByText(/当前后端未提供收益曲线接口/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("累计收益")).toBeInTheDocument();
    expect(screen.queryByText(/当前后端未提供收益曲线接口/)).not.toBeInTheDocument();
  });

  it("keeps account returns visible when no comparable index base exists", async () => {
    api.getReturnCurve.mockResolvedValueOnce({ ...curve, base_benchmark_close: null });
    renderCard();

    expect(await screen.findByText(/暂缺可对齐的指数收盘数据/)).toBeInTheDocument();
    expect(screen.getByText("+1.00%")).toBeInTheDocument();
    expect(screen.queryByText("+2.50%")).not.toBeInTheDocument();
  });

  it("keeps the last successful curve visible when a background refresh fails", async () => {
    api.getReturnCurve.mockRejectedValueOnce(new Error("网络错误"));
    renderCard(curve);

    expect(await screen.findByRole("alert")).toHaveTextContent("刷新失败：网络错误");
    expect(screen.getByText("+1.00%")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /累计收益曲线/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("explains late recording while showing the opening date as the return basis", async () => {
    api.getReturnCurve.mockResolvedValueOnce({ ...curve, days: curve.days.slice(-1) });
    renderCard();

    expect(await screen.findByText(/首条净值记录晚于开户日期/)).toBeInTheDocument();
    expect(screen.getByText("8/28 起")).toBeInTheDocument();
  });

  it("shows a failed equity write beside the retained curve and its timestamps", async () => {
    api.getReturnCurve.mockResolvedValueOnce({
      ...curve,
      recording_status: {
        trade_date: "2026-10-21",
        attempted_at: "2026-10-21T07:10:00Z",
        last_success_at: "2026-10-21T07:00:00Z",
        error_message: "数据库正忙，请稍后刷新账户。",
      },
    });
    renderCard();

    const warning = await screen.findByRole("alert", { name: "净值保存异常" });
    expect(warning).toHaveTextContent("2026-10-21 的最新净值未保存");
    expect(warning).toHaveTextContent("数据库正忙");
    expect(warning).toHaveTextContent("15:10:00");
    expect(warning).toHaveTextContent("15:00:00");
    expect(screen.getByRole("img", { name: /累计收益曲线/ })).toBeInTheDocument();
  });

  it("still warns when the first equity write failed and there is no history yet", async () => {
    api.getReturnCurve.mockResolvedValueOnce({
      ...curve,
      days: [],
      recording_status: {
        trade_date: "2026-10-21",
        attempted_at: "2026-10-21T07:10:00Z",
        last_success_at: null,
        error_message: "存储空间不足，请清理磁盘后刷新账户。",
      },
    });
    renderCard();

    expect(await screen.findByRole("alert", { name: "净值保存异常" })).toHaveTextContent(
      "尚无保存成功的状态记录",
    );
    expect(screen.getByText(/还没有记录/)).toBeInTheDocument();
  });

  it("shows successful saving without a failure alert", async () => {
    api.getReturnCurve.mockResolvedValueOnce({
      ...curve,
      recording_status: {
        trade_date: "2026-10-21",
        attempted_at: "2026-10-21T07:10:00Z",
        last_success_at: "2026-10-21T07:10:00Z",
        error_message: null,
      },
    });
    renderCard();

    expect(await screen.findByText(/净值最近保存成功/)).toHaveTextContent("15:10:00");
    expect(screen.queryByRole("alert", { name: "净值保存异常" })).not.toBeInTheDocument();
  });
});
