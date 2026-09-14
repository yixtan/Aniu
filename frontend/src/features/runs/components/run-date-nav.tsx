import { useEffect, useRef } from "react";
import { CalendarIcon, CircleAlertIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { RunDay } from "@/lib/api-types";

/**
 * The days that produced runs, newest first.
 *
 * Newest on the left and older to the right, so the day you almost always want
 * is where the eye lands and history is a scroll away rather than a page away.
 * Days with no runs are simply absent: weekends and holidays are not gaps to
 * be drawn, and the server only reports days that happened.
 */

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"] as const;

function dayLabel(day: string) {
  const [, month, date] = day.split("-");
  return `${Number(month)}月${Number(date)}日`;
}

function weekdayLabel(day: string) {
  // Parsed as a plain date, not an instant, so the weekday cannot slip a day
  // in a zone behind UTC.
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return WEEKDAYS[new Date(year, month - 1, date).getDay()] ?? "";
}

export function RunDateNav({
  days,
  selectedDay,
  onSelectDay,
}: {
  days: RunDay[];
  selectedDay: string | null;
  onSelectDay: (day: string) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = listRef.current;
    if (container === null) return;
    const handleWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      const maxScrollLeft = container.scrollWidth - container.clientWidth;
      const next = Math.max(0, Math.min(maxScrollLeft, container.scrollLeft + event.deltaY));
      if (next !== container.scrollLeft) {
        event.preventDefault();
        container.scrollLeft = next;
      }
    };
    container.addEventListener("wheel", handleWheel, { passive: false });
    return () => container.removeEventListener("wheel", handleWheel);
  }, [days.length]);

  return (
    <div
      ref={listRef}
      data-testid="run-date-nav"
      aria-label="运行日期"
      className="flex min-w-0 items-center gap-1.5 overflow-x-auto overflow-y-hidden p-1 pb-2 [scrollbar-width:thin]"
    >
      {days.map((day) => {
        const isSelected = day.day === selectedDay;
        const wentWrong = day.analysis_failed + day.watch_failed;
        return (
          <button
            key={day.day}
            type="button"
            onClick={() => onSelectDay(day.day)}
            aria-pressed={isSelected}
            aria-label={`查看 ${day.day} 的运行日程`}
            title={`${day.day} · 操盘 ${day.analysis_total} 次 · 盯盘 ${day.watch_total} 次`}
            className="group focus-visible:ring-ring/50 w-[132px] shrink-0 rounded-md text-start outline-none focus-visible:ring-[3px]"
          >
            <span
              className={cn(
                "border-input flex h-[74px] flex-col rounded-md border bg-transparent px-3 py-2 shadow-xs transition-[background-color,border-color,box-shadow]",
                isSelected
                  ? "border-ring ring-ring/35 ring-[3px]"
                  : "group-hover:border-ring/60 group-hover:bg-muted/20",
              )}
            >
              <span className="flex items-start justify-between gap-2">
                <span className="min-w-0">
                  <span className="text-muted-foreground flex h-2.5 items-center gap-1 text-[8px] leading-none font-medium tracking-wide">
                    <CalendarIcon className="size-2.5 shrink-0" aria-hidden />
                    <span>{weekdayLabel(day.day)}</span>
                  </span>
                  <span className="text-foreground mt-1 block truncate text-[12px] leading-none font-semibold tracking-tight tabular-nums">
                    {dayLabel(day.day)}
                  </span>
                </span>
                {wentWrong > 0 ? (
                  <span
                    className="text-destructive flex shrink-0 items-center gap-0.5 text-[9px] leading-none font-semibold tabular-nums"
                    title={`${wentWrong} 次运行失败或中止`}
                  >
                    <CircleAlertIcon className="size-2.5" aria-hidden />
                    {wentWrong}
                  </span>
                ) : null}
              </span>

              <span className="divide-border/55 border-border/45 mt-auto grid grid-cols-2 divide-x border-t pt-1.5">
                <span className="flex min-w-0 flex-col pe-2">
                  <span className="text-muted-foreground text-[8px] leading-none">操盘</span>
                  <span className="text-foreground mt-1 text-[10px] leading-none font-semibold tabular-nums">
                    {day.analysis_total}
                  </span>
                </span>
                <span className="flex min-w-0 flex-col ps-2">
                  <span className="text-muted-foreground text-[8px] leading-none">盯盘</span>
                  <span className="text-foreground mt-1 text-[10px] leading-none font-semibold tabular-nums">
                    {day.watch_total}
                  </span>
                </span>
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
