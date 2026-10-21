import type { getReturnCurve } from "@/lib/api";

export type ReturnCurveData = Awaited<ReturnType<typeof getReturnCurve>>;
type CurveDay = ReturnCurveData["days"][number];
export type CurveFill = CurveDay["fills"][number];

export type CurveRange = "1m" | "3m" | "all";

export const CURVE_RANGES: { id: CurveRange; label: string }[] = [
  { id: "1m", label: "近 1 月" },
  { id: "3m", label: "近 3 月" },
  { id: "all", label: "全部" },
];

export type CurvePoint = {
  date: string;
  totalAsset: number;
  /** Change since the start of the range, as a ratio: -0.031 is -3.1%. */
  accountReturn: number;
  benchmarkReturn: number | null;
  /** Change since the previous recorded value, in yuan. */
  dayProfit: number;
  /** Holdings as a share of the account. */
  positionRatio: number;
  isClose: boolean;
  reconstructed: boolean;
  fills: CurveFill[];
};

export type CurveSummary = {
  startDate: string | null;
  accountReturn: number | null;
  benchmarkReturn: number | null;
  benchmarkDate: string | null;
  /** The deepest fall from a previous high inside the range, as a ratio ≤ 0. */
  maxDrawdown: number | null;
  positionRatio: number | null;
};

export type Curve = { points: CurvePoint[]; summary: CurveSummary };

const EMPTY_SUMMARY: CurveSummary = {
  startDate: null,
  accountReturn: null,
  benchmarkReturn: null,
  benchmarkDate: null,
  maxDrawdown: null,
  positionRatio: null,
};

function monthsBefore(isoDate: string, months: number) {
  const [year = 1970, month = 1, day = 1] = isoDate.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1 - months, 1));
  const lastDay = new Date(Date.UTC(shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, 0));
  shifted.setUTCDate(Math.min(day, lastDay.getUTCDate()));
  return shifted.toISOString().slice(0, 10);
}

function firstInRange(days: CurveDay[], range: CurveRange) {
  const last = days.at(-1);
  if (range === "all" || last === undefined) return 0;
  const cutoff = monthsBefore(last.date, range === "1m" ? 1 : 3);
  const index = days.findIndex((day) => day.date > cutoff);
  return index < 0 ? 0 : index;
}

/**
 * The days of one range, with both lines measured from the same starting point.
 *
 * That point is the close just before the range: the line shows what happened
 * inside it, so switching ranges re-zeroes both lines rather than slicing a
 * curve that started somewhere else. A range reaching back to the first day
 * starts from the money the account opened with and the index's close the day
 * before.
 */
export function buildCurve(data: ReturnCurveData, range: CurveRange): Curve {
  const days = data.days;
  const start = firstInRange(days, range);
  const before = start > 0 ? days[start - 1] : undefined;
  const inRange = days.slice(start);
  const first = inRange[0];
  if (first === undefined) return { points: [], summary: EMPTY_SUMMARY };

  const baseAsset = before?.total_asset ?? data.initial_capital ?? first.total_asset;
  // Both lines must share their starting date. Substituting a later index
  // close would silently measure a different period from the account.
  const baseClose = (before ? before.benchmark_close : data.base_benchmark_close) ?? null;

  let previousAsset = baseAsset;
  let peak = baseAsset;
  let maxDrawdown = 0;
  const points = inRange.map((day) => {
    const close = day.benchmark_close ?? null;
    peak = Math.max(peak, day.total_asset);
    maxDrawdown = Math.min(maxDrawdown, day.total_asset / peak - 1);
    const point: CurvePoint = {
      date: day.date,
      totalAsset: day.total_asset,
      accountReturn: day.total_asset / baseAsset - 1,
      benchmarkReturn: close !== null && baseClose ? close / baseClose - 1 : null,
      dayProfit: day.total_asset - previousAsset,
      positionRatio: day.total_asset > 0 ? day.market_value / day.total_asset : 0,
      isClose: day.is_close,
      reconstructed: day.source === "reconstructed",
      fills: day.fills,
    };
    previousAsset = day.total_asset;
    return point;
  });

  const last = points.at(-1);
  const lastBenchmark = [...points].reverse().find((point) => point.benchmarkReturn !== null);
  return {
    points,
    summary: {
      startDate: before ? first.date : (data.open_date ?? first.date),
      accountReturn: last?.accountReturn ?? null,
      benchmarkReturn: lastBenchmark?.benchmarkReturn ?? null,
      benchmarkDate: lastBenchmark?.date ?? null,
      maxDrawdown: points.length < 2 ? null : maxDrawdown,
      positionRatio: last?.positionRatio ?? null,
    },
  };
}

/** Round, evenly spaced axis values that cover every value given. */
export function percentTicks(values: number[], count = 4): number[] {
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 0.01;
  const rough = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? rough;
  const ticks: number[] = [];
  // Step until the top value is covered, not merely approached: a last tick
  // below the highest point would draw the line above the plot.
  for (let index = Math.floor(low / step); ticks.length < 50; index += 1) {
    const value = Number((index * step).toFixed(10));
    ticks.push(value);
    if (value >= high) break;
  }
  return ticks;
}
