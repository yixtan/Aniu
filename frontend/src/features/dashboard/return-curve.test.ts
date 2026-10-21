import { describe, expect, it } from "vitest";

import { buildCurve, percentTicks, type ReturnCurveData } from "./return-curve";

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

const data: ReturnCurveData = {
  benchmark_name: "沪深300",
  initial_capital: 1_000_000,
  open_date: "2026-08-28",
  base_date: "2026-08-27",
  base_benchmark_close: 4000,
  days: [
    day("2026-08-28", 1_000_000, 4040),
    day("2026-09-30", 1_020_000, 4200),
    day("2026-10-08", 990_000, 4100),
    day("2026-10-21", 1_010_000, 4150),
  ],
};

describe("buildCurve", () => {
  it("measures the whole history from the opening money and the day-before close", () => {
    const { points, summary } = buildCurve(data, "all");

    expect(points.map((point) => point.accountReturn)).toEqual([
      0,
      expect.closeTo(0.02),
      expect.closeTo(-0.01),
      expect.closeTo(0.01),
    ]);
    expect(points[0]?.benchmarkReturn).toBeCloseTo(0.01);
    expect(summary.accountReturn).toBeCloseTo(0.01);
    expect(summary.benchmarkReturn).toBeCloseTo(0.0375);
  });

  it("re-zeroes both lines at the close before a shorter range", () => {
    // One month back from 10/21 is 9/21, so the range opens on 9/30 and is
    // measured from 8/28's close.
    const { points, summary } = buildCurve(data, "1m");

    expect(points.map((point) => point.date)).toEqual(["2026-09-30", "2026-10-08", "2026-10-21"]);
    expect(points[0]?.accountReturn).toBeCloseTo(0.02);
    expect(points[0]?.benchmarkReturn).toBeCloseTo(4200 / 4040 - 1);
    expect(summary.accountReturn).toBeCloseTo(0.01);
  });

  it("takes the deepest fall from a high, not from the start", () => {
    // Peak 1,020,000 on 9/30, then 990,000: about -2.94%.
    expect(buildCurve(data, "all").summary.maxDrawdown).toBeCloseTo(990_000 / 1_020_000 - 1);
  });

  it("gives each day its profit against the close before it", () => {
    expect(buildCurve(data, "all").points.map((point) => point.dayProfit)).toEqual([
      0, 20_000, -30_000, 20_000,
    ]);
  });

  it("reports the holding share of the latest day", () => {
    expect(buildCurve(data, "all").summary.positionRatio).toBeCloseTo(50_000 / 1_010_000);
  });

  it("keeps the account line but does not change the index base when its close is missing", () => {
    const { points, summary } = buildCurve({ ...data, base_benchmark_close: null }, "all");

    expect(points.every((point) => point.benchmarkReturn === null)).toBe(true);
    expect(summary.benchmarkReturn).toBeNull();
    expect(summary.accountReturn).toBeCloseTo(0.01);
  });

  it("does not borrow an index base from inside a shorter range", () => {
    const days = [day("2026-08-28", 1_000_000, null), ...data.days.slice(1)];
    const { summary } = buildCurve({ ...data, days }, "1m");

    expect(summary.benchmarkReturn).toBeNull();
    expect(summary.accountReturn).toBeCloseTo(0.01);
  });

  it("dates an index summary that stops before the latest account value", () => {
    const days = [...data.days.slice(0, -1), day("2026-10-21", 1_010_000, null)];
    const { summary } = buildCurve({ ...data, days }, "all");

    expect(summary.benchmarkDate).toBe("2026-10-08");
    expect(summary.benchmarkReturn).toBeCloseTo(0.025);
  });

  it("clamps month-end ranges instead of rolling into the next month", () => {
    const days = [
      day("2026-09-30", 1_000_000, 4000),
      day("2026-10-01", 1_010_000, 4040),
      day("2026-10-31", 1_020_000, 4080),
    ];

    expect(buildCurve({ ...data, days }, "1m").points.map((point) => point.date)).toEqual([
      "2026-10-01",
      "2026-10-31",
    ]);
  });

  it("leaves a day without an index close without a benchmark value", () => {
    const withGap = { ...data, days: [day("2026-08-28", 1_000_000, null)] };

    expect(buildCurve(withGap, "all").points[0]?.benchmarkReturn).toBeNull();
  });

  it("is empty with no days", () => {
    const curve = buildCurve({ ...data, days: [] }, "all");

    expect(curve.points).toEqual([]);
    expect(curve.summary.accountReturn).toBeNull();
  });

  it("keeps the opening date as the return basis when recording starts late", () => {
    const { summary } = buildCurve({ ...data, days: data.days.slice(-1) }, "all");

    expect(summary.startDate).toBe("2026-08-28");
    expect(summary.accountReturn).toBeCloseTo(0.01);
  });
});

describe("percentTicks", () => {
  it("covers zero and every value with round steps", () => {
    const ticks = percentTicks([-0.031, 0.012, -0.103]);

    expect(ticks[0]).toBeLessThanOrEqual(-0.103);
    expect(ticks.at(-1)).toBeGreaterThanOrEqual(0.012);
    expect(ticks).toContain(0);
  });
});
