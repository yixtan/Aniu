import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BrainIcon,
  CoinsIcon,
  DatabaseIcon,
  MoonStarIcon,
  RefreshCwIcon,
  WorkflowIcon,
  type LucideIcon,
} from "lucide-react";

import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { systemStatusKeys } from "@/features/settings/query-keys";
import { getSystemStatus } from "@/lib/api";
import type { DailyStatus, DreamStatus, TokenDay } from "@/lib/api-types";
import { formatMonthDayTime, formatNumber, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"] as const;

/** "2026-09-09" → "09-09 周三", read as a calendar date wherever the viewer is. */
function formatDay(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  if (year === undefined || month === undefined || date === undefined) return day;
  const weekday = WEEKDAYS[new Date(year, month - 1, date).getDay()];
  return `${day.slice(5)} 周${weekday}`;
}

const alarm = "text-destructive";
const notice = "text-amber-600";

type StatCard = {
  title: string;
  icon: LucideIcon;
  value: string;
  valueLabel: string;
  valueClassName?: string;
  rows: { label: string; value: string; className?: string }[];
};

function todayCards(today: DailyStatus): StatCard[] {
  const failureRate =
    today.data_calls === 0 ? 0 : Math.round((today.data_call_failures / today.data_calls) * 100);
  return [
    {
      title: "运行",
      icon: WorkflowIcon,
      value: String(today.runs_completed),
      valueLabel: "次完成",
      rows: [
        {
          label: "失败",
          value: String(today.runs_failed),
          className: cn(today.runs_failed > 0 && alarm),
        },
        {
          label: "HTML 总结",
          value: `${today.summaries_html} / ${today.runs_completed}`,
          className: cn(today.summaries_html < today.runs_completed && alarm),
        },
      ],
    },
    {
      title: "数据工具",
      icon: DatabaseIcon,
      value: String(today.data_calls),
      valueLabel: "次调用",
      rows: [
        {
          label: "失败",
          value: String(today.data_call_failures),
          className: cn(today.data_call_failures > 0 && alarm),
        },
        { label: "失败率", value: `${failureRate}%`, className: cn(failureRate >= 10 && alarm) },
      ],
    },
    {
      title: "记忆",
      icon: BrainIcon,
      value: String(today.memory_writes),
      valueLabel: "条写入",
      // Runs that never wrote is the quiet failure worth a second look; it
      // is not wrong on its own, so it is a notice rather than an alarm.
      valueClassName: cn(today.runs_completed > 0 && today.memory_writes === 0 && notice),
      rows: [
        {
          label: "写入失败",
          value: String(today.memory_write_failures),
          className: cn(today.memory_write_failures > 0 && alarm),
        },
        { label: "读取", value: String(today.memory_reads) },
        { label: "查询种类", value: String(today.memory_distinct_queries) },
      ],
    },
    {
      title: "交易",
      icon: CoinsIcon,
      value: String(today.trades_completed),
      valueLabel: "笔下单",
      rows: [
        {
          label: "失败",
          value: String(today.trades_failed),
          className: cn(today.trades_failed > 0 && alarm),
        },
        { label: "Token", value: formatTokens(today.tokens) },
      ],
    },
  ];
}

function TodayCards({ today }: { today: DailyStatus }) {
  return (
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="今日状态">
      {todayCards(today).map((card) => (
        <Card key={card.title} className="gap-2 py-4">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-0">
            <CardTitle className="text-sm font-medium">{card.title}</CardTitle>
            <card.icon className="text-muted-foreground size-4" />
          </CardHeader>
          <CardContent>
            <div className={cn("text-2xl font-bold", card.valueClassName)}>{card.value}</div>
            <p className="text-muted-foreground text-xs">{card.valueLabel}</p>
            <dl className="mt-3 space-y-1.5 border-t pt-3">
              {card.rows.map((row) => (
                <div key={row.label} className="flex items-center justify-between gap-2 text-xs">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className={cn("font-medium", row.className)}>{row.value}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

function dreamBadge(status: string) {
  if (status === "completed") return <Badge variant="secondary">已完成</Badge>;
  if (status === "failed") return <Badge variant="destructive">失败</Badge>;
  if (status === "running") return <Badge variant="outline">进行中</Badge>;
  return <Badge variant="outline">待执行</Badge>;
}

function DreamCard({
  dream,
  memoryLive,
  memoryWithLineage,
}: {
  dream: DreamStatus | null;
  memoryLive: number;
  memoryWithLineage: number;
}) {
  return (
    <Card className="gap-2 py-4">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-0">
        <CardTitle className="text-sm font-medium">最近梦境</CardTitle>
        <MoonStarIcon className="text-muted-foreground size-4" />
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {dream === null ? (
          <p className="text-muted-foreground">还没有执行过梦境。</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">整理 {dream.target_date}</span>
              {dreamBadge(dream.status)}
              {dream.completed_at ? (
                <span className="text-muted-foreground text-xs">
                  {formatMonthDayTime(dream.completed_at)}
                </span>
              ) : null}
            </div>
            {dream.failure_reason ? (
              <p className={cn("text-xs", alarm)}>{dream.failure_reason}</p>
            ) : null}
            <p className="text-muted-foreground text-xs">
              新增 {dream.created} 条 · 更新 {dream.updated} 条 · 删除 {dream.deleted} 条
            </p>
          </>
        )}
        <p className="text-muted-foreground border-t pt-3 text-xs">
          记忆库现有 {memoryLive} 条，其中 {memoryWithLineage} 条记录了由哪些经验凝练而来
        </p>
      </CardContent>
    </Card>
  );
}

/** One hue, two steps: the bars are a magnitude, and today is the one to find. */
const BAR = "bg-slate-500 hover:bg-slate-600 dark:bg-slate-500 dark:hover:bg-slate-400";
const BAR_TODAY = "bg-slate-800 dark:bg-slate-200";
const BAR_IDLE = "bg-slate-200 dark:bg-slate-700";

function describeDay(day: TokenDay) {
  return `${formatDay(day.day)} · ${formatTokens(day.tokens)} · ${day.runs} 次运行`;
}

function TokenChart({ tokens }: { tokens: TokenDay[] }) {
  // Oldest on the left, today on the right — the way a cost is read.
  const series = [...tokens].reverse();
  const total = series.reduce((sum, day) => sum + day.tokens, 0);
  const runs = series.reduce((sum, day) => sum + day.runs, 0);
  const activeDays = series.filter((day) => day.runs > 0).length;
  const peak = Math.max(1, ...series.map((day) => day.tokens));
  const first = series[0];
  const last = series[series.length - 1];
  // The readout above the bars names whichever day is under the pointer, and
  // today when none is — one line of text instead of thirty tooltips.
  const [hovered, setHovered] = useState<number | null>(null);
  const focus = hovered === null ? last : series[hovered];

  return (
    <Card className="gap-2 py-4">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-0">
        <CardTitle className="text-sm font-medium">Token 消耗（近 {series.length} 天）</CardTitle>
        <CoinsIcon className="text-muted-foreground size-4" />
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">合计</dt>
            <dd className="font-medium">{formatTokens(total)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">有运行的天数</dt>
            <dd className="font-medium">{activeDays}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">日均</dt>
            <dd className="font-medium">
              {activeDays === 0 ? "--" : formatTokens(Math.round(total / activeDays))}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted-foreground">每次运行</dt>
            <dd className="font-medium">
              {runs === 0 ? "--" : formatTokens(Math.round(total / runs))}
            </dd>
          </div>
        </dl>
        <p className="text-xs tabular-nums" aria-live="polite">
          {focus ? describeDay(focus) : ""}
        </p>
        <ol
          className="border-border/70 flex h-28 items-end gap-1 border-b"
          aria-label={`每日 Token 消耗，合计 ${formatNumber(total)}`}
          onMouseLeave={() => setHovered(null)}
        >
          {series.map((day, index) => {
            const isToday = index === series.length - 1;
            const idle = day.tokens === 0;
            return (
              <li
                key={day.day}
                className="flex h-full min-w-0 flex-1 cursor-default items-end"
                aria-label={`${day.day} ${formatNumber(day.tokens)} tokens，${day.runs} 次运行`}
                onMouseEnter={() => setHovered(index)}
              >
                <span
                  className={cn(
                    "block w-full rounded-t-[3px] transition-colors",
                    idle ? BAR_IDLE : isToday ? BAR_TODAY : BAR,
                  )}
                  style={{ height: `${Math.max(idle ? 2 : 4, (day.tokens / peak) * 100)}%` }}
                />
              </li>
            );
          })}
        </ol>
        <div className="text-muted-foreground flex justify-between text-xs">
          <span>{first ? formatDay(first.day) : ""}</span>
          <span>{last ? formatDay(last.day) : ""}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function Count({ value, alarmWhen = false }: { value: number; alarmWhen?: boolean }) {
  return (
    <span
      className={cn(
        "tabular-nums",
        alarmWhen && alarm,
        value === 0 && !alarmWhen && "text-muted-foreground",
      )}
    >
      {value}
    </span>
  );
}

function DaysTable({ days }: { days: DailyStatus[] }) {
  return (
    <section className="space-y-2" aria-label="近 7 天">
      <h3 className="text-sm font-medium">近 {days.length} 天</h3>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>日期</TableHead>
              <TableHead className="text-right">运行</TableHead>
              <TableHead className="text-right">失败</TableHead>
              <TableHead className="text-right">HTML</TableHead>
              <TableHead className="text-right">下单</TableHead>
              <TableHead className="text-right">下单失败</TableHead>
              <TableHead className="text-right">数据调用</TableHead>
              <TableHead className="text-right">数据失败</TableHead>
              <TableHead className="text-right">记忆写入</TableHead>
              <TableHead className="text-right">写入失败</TableHead>
              <TableHead className="text-right">读取</TableHead>
              <TableHead className="text-right">查询种类</TableHead>
              <TableHead className="text-right">Token</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {days.map((row) => (
              <TableRow key={row.day}>
                <TableCell className="whitespace-nowrap">{formatDay(row.day)}</TableCell>
                <TableCell className="text-right">
                  <Count value={row.runs_completed} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.runs_failed} alarmWhen={row.runs_failed > 0} />
                </TableCell>
                <TableCell className="text-right">
                  <Count
                    value={row.summaries_html}
                    alarmWhen={row.summaries_html < row.runs_completed}
                  />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.trades_completed} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.trades_failed} alarmWhen={row.trades_failed > 0} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.data_calls} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.data_call_failures} alarmWhen={row.data_call_failures > 0} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.memory_writes} />
                </TableCell>
                <TableCell className="text-right">
                  <Count
                    value={row.memory_write_failures}
                    alarmWhen={row.memory_write_failures > 0}
                  />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.memory_reads} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.memory_distinct_queries} />
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatTokens(row.tokens)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

export function SystemStatusPanel() {
  const query = useQuery({
    queryKey: systemStatusKeys.overview,
    queryFn: getSystemStatus,
  });

  if (query.isLoading) {
    return <QueryLoadingState label="正在加载系统状态…" />;
  }
  if (query.isError || !query.data) {
    return (
      <QueryErrorState
        title="系统状态加载失败"
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const status = query.data;
  const today = status.days[0];

  return (
    <section className="space-y-4" aria-label="系统状态">
      <div className="flex items-center justify-between gap-3">
        <p className="text-muted-foreground text-sm">
          按上海时间分天统计。数据工具失败、HTML
          总结缺失、记忆写入失败，任何一项出现都比新功能重要。
        </p>
        <Button
          type="button"
          variant="outline"
          size="icon"
          title="刷新系统状态"
          aria-label="刷新系统状态"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCwIcon className={cn("size-4", query.isFetching && "animate-spin")} />
        </Button>
      </div>
      {today ? <TodayCards today={today} /> : null}
      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <DreamCard
          dream={status.latest_dream}
          memoryLive={status.memory_live}
          memoryWithLineage={status.memory_with_lineage}
        />
        <TokenChart tokens={status.tokens} />
      </div>
      <DaysTable days={status.days} />
    </section>
  );
}
