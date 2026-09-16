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
        // Watches are counted apart: eighty-odd a day, none of them an
        // analysis run, so they get their own rows rather than inflating the
        // ones above.
        { label: "盯盘", value: String(today.watches_completed) },
        {
          label: "盯盘失败",
          value: String(today.watches_failed),
          className: cn(today.watches_failed > 0 && alarm),
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
        // Inside the line above, not added to it. A provider bills a cache
        // hit at a fraction of fresh input, so a day that looks expensive is
        // often the same prompt resent — worth seeing next to the headline.
        ...(today.cached_tokens > 0
          ? [{ label: "其中缓存命中", value: formatTokens(today.cached_tokens) }]
          : []),
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

/** One figure in the summary row that opens a card. */
function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function DreamsCard({
  dreams,
  memoryLive,
  memoryDeleted,
  memoryWithLineage,
}: {
  dreams: DreamStatus[];
  memoryLive: number;
  memoryDeleted: number;
  memoryWithLineage: number;
}) {
  return (
    <Card className="gap-2 py-4">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-0">
        <CardTitle className="text-sm font-medium">
          {dreams.length > 0 ? `记忆梦境（最近 ${dreams.length} 次）` : "记忆梦境"}
        </CardTitle>
        <MoonStarIcon className="text-muted-foreground size-4" />
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
          <Stat label="记忆库" value={`${memoryLive} 条`} />
          <Stat label="已删除" value={`${memoryDeleted} 条`} />
          <Stat label="记录了血缘" value={`${memoryWithLineage} 条`} />
        </dl>
        {dreams.length === 0 ? (
          <p className="text-muted-foreground text-sm">还没有执行过梦境。</p>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>整理日期</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>完成时间</TableHead>
                  <TableHead className="text-right">新增</TableHead>
                  <TableHead className="text-right">更新</TableHead>
                  <TableHead className="text-right">删除</TableHead>
                  <TableHead className="text-right">Token</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dreams.map((dream) => (
                  <TableRow key={dream.target_date}>
                    <TableCell className="font-medium whitespace-nowrap">
                      {formatDay(dream.target_date)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col items-start gap-1">
                        {dreamBadge(dream.status)}
                        {dream.failure_reason ? (
                          <span className={cn("text-xs", alarm)}>{dream.failure_reason}</span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground whitespace-nowrap">
                      {dream.completed_at ? formatMonthDayTime(dream.completed_at) : "--"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Count value={dream.created} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Count value={dream.updated} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Count value={dream.deleted} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <div className="flex flex-col items-end">
                        <span>
                          {dream.total_tokens > 0 ? formatTokens(dream.total_tokens) : "--"}
                        </span>
                        {dream.cached_tokens > 0 ? (
                          <span className="text-muted-foreground text-xs">
                            缓存 {formatTokens(dream.cached_tokens)}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const BAR_IDLE = "bg-muted";

/** One hue per provider — distinct rather than graded, because these are
 *  different things being compared, not more and less of one thing.
 *
 *  No `dark:` variants, deliberately. This app has no dark mode: one palette
 *  on `:root`, no toggle, nothing that sets a `dark` class. Tailwind's `dark:`
 *  follows the operating system regardless, so adding it here would repaint
 *  these bars on a machine set to dark while the page around them stayed
 *  white. Half a dark mode is worse than none. */
const CHANNEL_COLORS = [
  "bg-sky-500",
  "bg-violet-500",
  "bg-amber-500",
  "bg-emerald-500",
  "bg-rose-500",
  "bg-teal-500",
] as const;
const BAR_UNRECORDED = "bg-muted-foreground/40";

function channelKey(channel: { channel_id: number | null }) {
  return channel.channel_id === null ? "none" : String(channel.channel_id);
}

/**
 * A colour per provider, fixed for the whole window.
 *
 * Assigned by total spend so the heaviest provider is the same colour every
 * time the page is opened; a colour that moved between visits would make the
 * chart unreadable at a glance, which is the only thing it is for.
 */
function assignChannelColors(series: TokenDay[]) {
  const totals = new Map<string, { name: string; tokens: number }>();
  for (const day of series) {
    for (const channel of day.channels) {
      const key = channelKey(channel);
      const seen = totals.get(key);
      totals.set(key, {
        name: channel.name,
        tokens: (seen?.tokens ?? 0) + channel.tokens,
      });
    }
  }
  const ranked = [...totals.entries()].sort((left, right) => right[1].tokens - left[1].tokens);
  const colors = new Map<string, string>();
  // Rank across the whole window, not within a day. Every bar stacks in this
  // one order, so a colour keeps its height on the bar as well as its place in
  // the legend — ordering each day by its own heaviest provider put purple
  // under blue on one day and over it on the next.
  const rank = new Map(ranked.map(([key], index) => [key, index]));
  let next = 0;
  for (const [key] of ranked) {
    const hue = CHANNEL_COLORS[next++ % CHANNEL_COLORS.length] ?? BAR_UNRECORDED;
    colors.set(key, key === "none" ? BAR_UNRECORDED : hue);
  }
  return { colors, ranked, rank };
}

/** A day's providers in the legend's order, first at the bottom of the bar. */
function inLegendOrder(day: TokenDay, rank: Map<string, number>) {
  return [...day.channels].sort(
    (left, right) =>
      (rank.get(channelKey(left)) ?? 0) - (rank.get(channelKey(right)) ?? 0),
  );
}

function describeDay(day: TokenDay, rank: Map<string, number>) {
  const parts = [`${formatDay(day.day)} · ${formatTokens(day.tokens)} · ${day.runs} 次运行`];
  if (day.watch_tokens > 0 || day.watches > 0) {
    parts.push(`盯盘 ${formatTokens(day.watch_tokens)} · ${day.watches} 次`);
  }
  if (day.dream_tokens > 0) parts.push(`梦境 ${formatTokens(day.dream_tokens)}`);
  if (day.cached_tokens > 0) parts.push(`其中缓存 ${formatTokens(day.cached_tokens)}`);
  if (day.channels.length > 0) {
    parts.push(
      inLegendOrder(day, rank)
        .map((channel) => `${channel.name} ${formatTokens(channel.tokens)}`)
        .join(" · "),
    );
  }
  return parts.join("，");
}

function TokenChart({ tokens }: { tokens: TokenDay[] }) {
  // Oldest on the left, today on the right — the way a cost is read.
  const series = [...tokens].reverse();
  const dayTotal = (day: TokenDay) => day.tokens + day.watch_tokens + day.dream_tokens;
  const runTotal = series.reduce((sum, day) => sum + day.tokens, 0);
  const watchTotal = series.reduce((sum, day) => sum + day.watch_tokens, 0);
  const dreamTotal = series.reduce((sum, day) => sum + day.dream_tokens, 0);
  const total = runTotal + watchTotal + dreamTotal;
  // Already counted in the three above, never added to them: the provider
  // reports a cache hit inside its own total. Shown so the headline is not
  // read as fresh spend — on 2026-09-16, 86% of the dream was a cache hit.
  const cachedTotal = series.reduce((sum, day) => sum + day.cached_tokens, 0);
  const runs = series.reduce((sum, day) => sum + day.runs, 0);
  const watches = series.reduce((sum, day) => sum + day.watches, 0);
  const activeDays = series.filter((day) => day.runs > 0 || day.watches > 0).length;
  const peak = Math.max(1, ...series.map(dayTotal));
  const { colors, ranked, rank } = assignChannelColors(series);
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
          <Stat label="合计" value={formatTokens(total)} />
          <Stat label="其中盯盘" value={formatTokens(watchTotal)} />
          <Stat label="其中梦境" value={formatTokens(dreamTotal)} />
          <Stat label="其中缓存命中" value={formatTokens(cachedTotal)} />
          <Stat label="有运行的天数" value={String(activeDays)} />
          <Stat
            label="日均"
            value={activeDays === 0 ? "--" : formatTokens(Math.round(total / activeDays))}
          />
          <Stat
            label="每次运行"
            value={
              runs === 0 ? "--" : formatTokens(Math.round((runTotal + dreamTotal) / runs))
            }
          />
          <Stat
            label="每次盯盘"
            value={watches === 0 ? "--" : formatTokens(Math.round(watchTotal / watches))}
          />
        </dl>
        {ranked.length > 0 ? (
          <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" aria-label="渠道">
            {ranked.map(([key, entry]) => (
              <li key={key} className="text-muted-foreground flex items-center gap-1.5">
                <span
                  aria-hidden
                  className={cn("size-2 shrink-0 rounded-[2px]", colors.get(key))}
                />
                <span>{entry.name}</span>
                <span className="tabular-nums">{formatTokens(entry.tokens)}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs tabular-nums" aria-live="polite">
          {focus ? describeDay(focus, rank) : ""}
        </p>
        <ol
          className="border-border/70 flex h-28 items-end gap-1 border-b"
          aria-label={`每日 Token 消耗，合计 ${formatNumber(total)}`}
          onMouseLeave={() => setHovered(null)}
        >
          {series.map((day, index) => {
            const combined = dayTotal(day);
            const idle = combined === 0;
            return (
              <li
                key={day.day}
                className="flex h-full min-w-0 flex-1 cursor-default items-end"
                aria-label={
                  `${day.day} 运行 ${formatNumber(day.tokens)} tokens、` +
                  `盯盘 ${formatNumber(day.watch_tokens)} tokens、` +
                  `梦境 ${formatNumber(day.dream_tokens)} tokens，` +
                  `其中缓存命中 ${formatNumber(day.cached_tokens)} tokens，` +
                  `${day.runs} 次运行，${day.watches} 次盯盘`
                }
                onMouseEnter={() => setHovered(index)}
              >
                {/* Providers stack heaviest at the bottom. Analyses, watches
                    and dreams all sit inside these bands — the day's spend
                    split by who was asked, not by what was asked. */}
                <span
                  className="flex w-full flex-col justify-end"
                  style={{ height: `${Math.max(idle ? 2 : 4, (combined / peak) * 100)}%` }}
                >
                  {/* Reversed for rendering only: the first child of a column
                      is the top one, and the legend's first provider belongs
                      at the bottom. */}
                  {inLegendOrder(day, rank)
                    .reverse()
                    .map((channel, position, bands) => (
                    <span
                      key={channelKey(channel)}
                      data-channel={channel.name}
                      className={cn(
                        "block w-full",
                        position === 0 ? "rounded-t-[3px]" : "",
                        // The bottom band takes the remainder so rounding
                        // cannot leave a hairline gap under the stack.
                        position === bands.length - 1 ? "flex-1" : "",
                        colors.get(channelKey(channel)) ?? BAR_UNRECORDED,
                      )}
                      style={
                        position === bands.length - 1
                          ? undefined
                          : { height: `${(channel.tokens / combined) * 100}%` }
                      }
                    />
                  ))}
                  {idle || day.channels.length === 0 ? (
                    <span className={cn("block w-full flex-1 rounded-t-[3px]", BAR_IDLE)} />
                  ) : null}
                </span>
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
              <TableHead className="text-right">盯盘</TableHead>
              <TableHead className="text-right">盯盘失败</TableHead>
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
                  <Count value={row.watches_completed} />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.watches_failed} alarmWhen={row.watches_failed > 0} />
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
      <DreamsCard
        dreams={status.dreams}
        memoryLive={status.memory_live}
        memoryDeleted={status.memory_deleted}
        memoryWithLineage={status.memory_with_lineage}
      />
      <TokenChart tokens={status.tokens} />
      <DaysTable days={status.days} />
    </section>
  );
}
