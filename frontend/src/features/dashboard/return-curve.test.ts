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

  it("falls back to the first close it has when the base close is missing", () => {
    const { points } = buildCurve({ ...data, base_benchmark_close: null }, "all");

    expect(points[0]?.benchmarkReturn).toBe(0);
    expect(points[1]?.benchmarkReturn).toBeCloseTo(4200 / 4040 - 1);
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
});

describe("percentTicks", () => {
  it("covers zero and every value with round steps", () => {
    const ticks = percentTicks([-0.031, 0.012, -0.103]);

    expect(ticks[0]).toBeLessThanOrEqual(-0.103);
    expect(ticks.at(-1)).toBeGreaterThanOrEqual(0.012);
    expect(ticks).toContain(0);
  });
});
