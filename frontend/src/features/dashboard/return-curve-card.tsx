import { useQuery } from "@tanstack/react-query";
import { TrendingUpIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { accountKeys } from "@/features/dashboard/query-keys";
import {
  CURVE_RANGES,
  buildCurve,
  percentTicks,
  type CurvePoint,
  type CurveRange,
  type ReturnCurveData,
} from "@/features/dashboard/return-curve";
import { getReturnCurve } from "@/lib/api";
import { formatCurrency, formatNumber, formatPercent, getErrorMessage } from "@/lib/format";
import { cn } from "@/lib/utils";

const CHART_HEIGHT = 200;
const BARS_HEIGHT = 44;
const PAD = { left: 46, right: 12, top: 10, bottom: 22 };
// Matches the tooltip's w-56, so it can be kept inside the card.
const TOOLTIP_WIDTH = 224;
// Below this average holding, say outright why the account's line is flatter
// than the index: a low line next to the index otherwise reads as a verdict.
const LOW_POSITION = 0.3;

function changeTone(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return undefined;
  return value > 0 ? "text-destructive" : "text-chart-2";
}

function plainPercent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${(value * 100).toFixed(2)}%`;
}

function shortDate(isoDate: string) {
  const [, month, day] = isoDate.split("-");
  return `${Number(month)}/${Number(day)}`;
}

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      setWidth(entries[0]?.contentRect.width ?? element.clientWidth);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ReturnCurveCard() {
  const query = useQuery({ queryKey: accountKeys.returnCurve(), queryFn: getReturnCurve });
  const [range, setRange] = useState<CurveRange>("all");

  return (
    <Card className="shrink-0 gap-3">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <TrendingUpIcon className="text-muted-foreground size-4" />
          收益曲线
        </CardTitle>
        <div className="flex gap-1" role="group" aria-label="时间范围">
          {CURVE_RANGES.map((option) => (
            <Button
              key={option.id}
              type="button"
              size="xs"
              variant={range === option.id ? "secondary" : "ghost"}
              aria-pressed={range === option.id}
              onClick={() => setRange(option.id)}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <p className="text-muted-foreground py-10 text-center text-sm">正在加载收益曲线…</p>
        ) : query.isError && query.data === undefined ? (
          <div className="flex flex-col items-center gap-2 py-8 text-sm">
            <p className="text-muted-foreground">
              收益曲线加载失败：{getErrorMessage(query.error)}
            </p>
            {query.error && "status" in query.error && query.error.status === 404 ? (
              <p className="text-muted-foreground text-center text-xs">
                当前后端未提供收益曲线接口，请确认前后端已更新到同一版本并重启后端。
              </p>
            ) : null}
            <Button type="button" size="xs" variant="outline" onClick={() => void query.refetch()}>
              重试
            </Button>
          </div>
        ) : (
          <>
            {query.isError ? (
              <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 text-sm">
                <p className="text-muted-foreground">
                  正在显示上次成功加载的收益曲线；刷新失败：{getErrorMessage(query.error)}
                </p>
                <Button
                  type="button"
                  size="xs"
                  variant="outline"
                  onClick={() => void query.refetch()}
                >
                  重试
                </Button>
              </div>
            ) : null}
            <ReturnCurveBody data={query.data} range={range} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ReturnCurveBody({ data, range }: { data: ReturnCurveData; range: CurveRange }) {
  const curve = useMemo(() => buildCurve(data, range), [data, range]);
  const { points, summary } = curve;

  const first = points[0];
  const last = points.at(-1);
  if (first === undefined || last === undefined) {
    return (
      <p className="text-muted-foreground py-8 text-center text-sm">
        还没有记录。每个交易日会自动记下账户的总资产，有了第一个交易日的数据后，这里就会画出曲线。
      </p>
    );
  }

  const averagePosition =
    points.reduce((total, point) => total + point.positionRatio, 0) / points.length;
  const hasReconstructed = points.some((point) => point.reconstructed);
  const hasBenchmark = points.some((point) => point.benchmarkReturn !== null);
  const hasBenchmarkGaps = points.some((point) => point.benchmarkReturn === null);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Stat
          label="累计收益"
          value={formatPercent(summary.accountReturn)}
          valueClassName={changeTone(summary.accountReturn)}
          hint={`${shortDate(summary.startDate ?? first.date)} 起`}
        />
        <Stat
          label={`同期${data.benchmark_name}`}
          value={formatPercent(summary.benchmarkReturn)}
          valueClassName={changeTone(summary.benchmarkReturn)}
          hint={
            summary.benchmarkDate
              ? `截至 ${shortDate(summary.benchmarkDate)} 收盘`
              : "暂无可对齐的指数数据"
          }
        />
        <Stat
          label="最大回撤"
          value={formatPercent(summary.maxDrawdown)}
          hint="从最高点往下跌得最多的一次"
        />
        <Stat
          label="当前仓位"
          value={plainPercent(summary.positionRatio)}
          hint="持仓市值占总资产"
        />
      </div>
      <CurveChart points={points} benchmarkName={data.benchmark_name} />
      <div className="text-muted-foreground space-y-1 text-xs">
        {data.open_date && data.days[0] && data.days[0].date > data.open_date ? (
          <p>首条净值记录晚于开户日期，累计收益仍从开户资金计算；此前的变化无法逐日展示。</p>
        ) : null}
        {averagePosition < LOW_POSITION ? (
          <p>
            这段时间平均仓位约 {plainPercent(averagePosition)}
            ，大部分资金是现金，所以账户的涨跌会比指数小很多。
          </p>
        ) : null}
        {hasReconstructed ? <p>浅色那段是事后用成交记录推算的，不是当天实时记下的。</p> : null}
        {!hasBenchmark ? (
          <p>暂缺可对齐的指数收盘数据，暂不显示指数收益，账户曲线仍可查看。</p>
        ) : hasBenchmarkGaps ? (
          <p>部分日期缺少指数收盘数据，指数曲线在缺失处断开；指数收益截至上方标注的日期。</p>
        ) : null}
        {!last.isClose ? <p>最后一个点尚未记录到收盘，显示的是当天最后一次记录。</p> : null}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  valueClassName,
}: {
  label: string;
  value: string;
  hint: string;
  valueClassName?: string | undefined;
}) {
  return (
    <div className="bg-muted/40 rounded-md px-3 py-2">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={cn("text-lg font-semibold tabular-nums", valueClassName)}>{value}</p>
      <p className="text-muted-foreground text-[11px] leading-tight">{hint}</p>
    </div>
  );
}

function segments(points: CurvePoint[], keep: (point: CurvePoint) => boolean) {
  // Runs of consecutive kept points. A run starts one point early so that two
  // kinds of line meet at the boundary instead of leaving a gap between them.
  const runs: number[][] = [];
  let current: number[] = [];
  points.forEach((point, index) => {
    if (keep(point)) {
      if (current.length === 0 && index > 0) current.push(index - 1);
      current.push(index);
    } else if (current.length > 0) {
      runs.push(current);
      current = [];
    }
  });
  if (current.length > 0) runs.push(current);
  return runs;
}

function CurveChart({ points, benchmarkName }: { points: CurvePoint[]; benchmarkName: string }) {
  const [containerRef, measured] = useElementWidth<HTMLDivElement>();
  const width = Math.max(measured, 280);
  const [active, setActive] = useState<number | null>(null);

  const values = points.flatMap((point) =>
    point.benchmarkReturn === null
      ? [point.accountReturn]
      : [point.accountReturn, point.benchmarkReturn],
  );
  const ticks = percentTicks(values);
  const low = ticks[0] ?? 0;
  const high = ticks.at(-1) ?? 0;
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = CHART_HEIGHT - PAD.top - PAD.bottom;
  const x = (index: number) =>
    points.length === 1
      ? PAD.left + plotWidth / 2
      : PAD.left + (index * plotWidth) / (points.length - 1);
  const y = (value: number) => PAD.top + ((high - value) / (high - low || 1)) * plotHeight;
  const path = (indexes: number[], value: (point: CurvePoint) => number | null) =>
    indexes
      .map((index, order) => {
        const point = points[index];
        const at = point === undefined ? null : value(point);
        return at === null ? "" : `${order === 0 ? "M" : "L"}${x(index)},${y(at)}`;
      })
      .join("");

  const recordedRuns = segments(points, (point) => !point.reconstructed);
  const reconstructedRuns = segments(points, (point) => point.reconstructed);
  const benchmarkRuns = segments(points, (point) => point.benchmarkReturn !== null).map((run) =>
    run.filter((index) => (points[index]?.benchmarkReturn ?? null) !== null),
  );
  const labelEvery = Math.max(
    1,
    Math.ceil(points.length / Math.max(2, Math.floor(plotWidth / 70))),
  );
  const maxPosition = Math.max(
    0.1,
    Math.ceil(Math.max(...points.map((p) => p.positionRatio)) * 10) / 10,
  );
  const barWidth = Math.max(2, Math.min(14, (plotWidth / points.length) * 0.6));

  const pick = (event: PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const relative = ((event.clientX - bounds.left) / bounds.width) * plotWidth;
    const index =
      points.length === 1 ? 0 : Math.round((relative / plotWidth) * (points.length - 1));
    setActive(Math.min(points.length - 1, Math.max(0, index)));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowLeft" ? -1 : 1;
    setActive((current) =>
      Math.min(points.length - 1, Math.max(0, (current ?? points.length - 1) + step)),
    );
  };

  const shown = active === null ? null : points[active];

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="text-muted-foreground mb-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-sky-600" />
          AI 账户
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 border-t-2 border-dashed border-slate-400" />
          {benchmarkName}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2 rounded-full bg-sky-600" />
          有成交的日子
        </span>
      </div>
      <div
        tabIndex={0}
        role="img"
        aria-label={`AI 账户与${benchmarkName}的累计收益曲线，共 ${points.length} 个交易日。左右方向键可以逐日查看。`}
        className="focus-visible:ring-ring rounded-sm outline-none focus-visible:ring-2"
        onKeyDown={onKeyDown}
        onBlur={() => setActive(null)}
      >
        <svg
          width={width}
          height={CHART_HEIGHT}
          className="block overflow-visible"
          aria-hidden="true"
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(tick)}
                y2={y(tick)}
                className={tick === 0 ? "stroke-slate-400/70" : "stroke-slate-300/50"}
                strokeWidth={1}
              />
              <text
                x={PAD.left - 6}
                y={y(tick)}
                textAnchor="end"
                dominantBaseline="middle"
                className="fill-muted-foreground text-[11px] tabular-nums"
              >
                {`${Number((tick * 100).toFixed(2))}%`}
              </text>
            </g>
          ))}
          {points.map((point, index) =>
            index % labelEvery === 0 || index === points.length - 1 ? (
              <text
                key={point.date}
                x={x(index)}
                y={CHART_HEIGHT - 4}
                textAnchor="middle"
                className="fill-muted-foreground text-[11px] tabular-nums"
              >
                {shortDate(point.date)}
              </text>
            ) : null,
          )}
          {benchmarkRuns.map((run) => (
            <path
              key={`b${run[0]}`}
              d={path(run, (point) => point.benchmarkReturn)}
              fill="none"
              strokeWidth={1.75}
              strokeDasharray="5 4"
              strokeLinecap="round"
              className="stroke-slate-400"
            />
          ))}
          <g className="text-sky-600">
            {points.length === 1 && points[0] && points[0].fills.length === 0 ? (
              <circle
                cx={x(0)}
                cy={y(points[0].accountReturn)}
                r={3.5}
                className="fill-background"
                stroke="currentColor"
                strokeWidth={2}
              />
            ) : null}
            {reconstructedRuns.map((run) => (
              <path
                key={`r${run[0]}`}
                d={path(run, (point) => point.accountReturn)}
                fill="none"
                stroke="currentColor"
                strokeOpacity={0.45}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            ))}
            {recordedRuns.map((run) => (
              <path
                key={`a${run[0]}`}
                d={path(run, (point) => point.accountReturn)}
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            ))}
            {points.map((point, index) =>
              point.fills.length > 0 ? (
                <circle
                  key={point.date}
                  cx={x(index)}
                  cy={y(point.accountReturn)}
                  r={3.5}
                  fill="currentColor"
                  className="stroke-background"
                  strokeWidth={1.5}
                />
              ) : null,
            )}
          </g>
          {shown && active !== null ? (
            <line
              x1={x(active)}
              x2={x(active)}
              y1={PAD.top}
              y2={CHART_HEIGHT - PAD.bottom}
              className="stroke-slate-400"
              strokeDasharray="2 3"
            />
          ) : null}
          <rect
            x={PAD.left}
            y={0}
            width={plotWidth}
            height={CHART_HEIGHT}
            fill="transparent"
            onPointerMove={pick}
            onPointerDown={pick}
            onPointerLeave={(event) => {
              if (event.pointerType === "mouse") setActive(null);
            }}
          />
        </svg>
        <p className="text-muted-foreground mt-2 mb-1 text-xs">每日仓位（持仓市值占总资产）</p>
        <svg width={width} height={BARS_HEIGHT} className="block" aria-hidden="true">
          <text
            x={PAD.left - 6}
            y={6}
            textAnchor="end"
            dominantBaseline="middle"
            className="fill-muted-foreground text-[11px] tabular-nums"
          >
            {`${Math.round(maxPosition * 100)}%`}
          </text>
          {points.map((point, index) => {
            const height = (point.positionRatio / maxPosition) * (BARS_HEIGHT - 6);
            return (
              <rect
                key={point.date}
                x={x(index) - barWidth / 2}
                y={BARS_HEIGHT - height}
                width={barWidth}
                height={Math.max(height, point.positionRatio > 0 ? 1 : 0)}
                rx={1.5}
                className={cn("fill-sky-500/35", index === active && "fill-sky-600/70")}
              />
            );
          })}
        </svg>
      </div>
      {shown && active !== null ? (
        <DayTooltip point={shown} benchmarkName={benchmarkName} left={x(active)} width={width} />
      ) : null}
    </div>
  );
}

function DayTooltip({
  point,
  benchmarkName,
  left,
  width,
}: {
  point: CurvePoint;
  benchmarkName: string;
  left: number;
  width: number;
}) {
  // Beside the day, on whichever side has room — and never past either edge:
  // on a phone neither side may, and an overhang scrolls the whole page.
  const beside = left > width / 2 ? left - 8 - TOOLTIP_WIDTH : left + 8;
  const offset = Math.min(Math.max(0, beside), Math.max(0, width - TOOLTIP_WIDTH));
  const fills = point.fills.slice(0, 3);
  return (
    <div
      role="status"
      className="bg-popover text-popover-foreground pointer-events-none absolute top-6 z-10 w-56 rounded-md border px-3 py-2 text-xs shadow-md"
      style={{ left: offset }}
    >
      <p className="mb-1 font-medium">
        {point.date}
        {point.reconstructed ? "（推算）" : !point.isClose ? "（盘中）" : ""}
      </p>
      <TooltipRow label="总资产" value={formatCurrency(point.totalAsset)} />
      <TooltipRow
        label="较上一记录盈亏"
        value={formatCurrency(point.dayProfit)}
        valueClassName={changeTone(point.dayProfit)}
      />
      <TooltipRow
        label="AI 账户"
        value={formatPercent(point.accountReturn)}
        valueClassName={changeTone(point.accountReturn)}
      />
      <TooltipRow
        label={benchmarkName}
        value={formatPercent(point.benchmarkReturn)}
        valueClassName={changeTone(point.benchmarkReturn)}
      />
      <TooltipRow label="仓位" value={plainPercent(point.positionRatio)} />
      {fills.length > 0 ? (
        <div className="mt-1.5 space-y-0.5 border-t pt-1.5">
          {fills.map((fill, index) => (
            <p key={`${fill.symbol}-${index}`}>
              {fill.direction === "SELL" ? "卖出" : "买入"} {fill.stock_name}{" "}
              {formatNumber(fill.quantity)} 股 @{formatCurrency(fill.price)}
            </p>
          ))}
          {point.fills.length > fills.length ? (
            <p className="text-muted-foreground">另有 {point.fills.length - fills.length} 笔</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TooltipRow({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string | undefined;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-medium tabular-nums", valueClassName)}>{value}</span>
    </div>
  );
}
