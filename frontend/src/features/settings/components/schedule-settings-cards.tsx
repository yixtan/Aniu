import { useMemo, useState, type ReactNode } from "react";
import {
  CheckCircle2Icon,
  CircleDashedIcon,
  Clock3Icon,
  RefreshCwIcon,
  SlidersHorizontalIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";

import type {
  CreateSchedulePayload,
  StrategySchedule,
  UpdateSchedulePayload,
} from "@/lib/api-types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { SectionLabel } from "@/features/settings/components/section-label";

// From the request type, not the response: the response spells `task_type`
// as a plain string while the request carries the literal union, and the
// union is what makes indexing the cadence table exhaustive.
export type ScheduleKind = CreateSchedulePayload["task_type"];

type Cadence = {
  label: string;
  minIntervalMinutes: number;
  defaultIntervalMinutes: number;
  maxCustomTimes: number;
  sessions: { start: number; end: number }[];
  // The watch's first pass of each session is one interval after the open,
  // so it never races the analysis run that writes the plan it reads.
  startsOneIntervalLate?: boolean;
};

// A preview-only mirror of the backend cadence table. The backend is the
// authority and validates every save; this only decides what the page shows
// before the save. The two kinds differ in more than frequency: an analysis
// run takes minutes and stops well short of the bell, while an order watch is
// a couple of tool calls and the close is exactly when it earns its keep.
const CADENCES: Record<ScheduleKind, Cadence> = {
  market_analysis: {
    label: "操盘",
    minIntervalMinutes: 15,
    defaultIntervalMinutes: 15,
    maxCustomTimes: 48,
    sessions: [
      { start: 9 * 60 + 30, end: 11 * 60 + 20 },
      { start: 13 * 60, end: 14 * 60 + 50 },
    ],
  },
  order_watch: {
    label: "盯盘",
    minIntervalMinutes: 3,
    defaultIntervalMinutes: 3,
    maxCustomTimes: 96,
    sessions: [
      { start: 9 * 60 + 30, end: 11 * 60 + 30 },
      { start: 13 * 60, end: 15 * 60 },
    ],
    startsOneIntervalLate: true,
  },
};

// A preview-only mirror of the backend's fixed analysis timetable. One row per
// allowed interval, written out; the backend re-derives each row from its rule
// in a test, so this copy is checked against that source by hand, not code.
const ANALYSIS_INTERVAL_CHOICES = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60] as const;
const ANALYSIS_TIMETABLE: Record<(typeof ANALYSIS_INTERVAL_CHOICES)[number], string[]> = {
  15: ["09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15",
       "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45"],
  20: ["09:30", "09:50", "10:10", "10:30", "10:50", "11:10",
       "13:00", "13:20", "13:40", "14:00", "14:20", "14:40"],
  25: ["09:30", "09:55", "10:20", "10:45", "11:10",
       "13:00", "13:25", "13:50", "14:15", "14:40"],
  30: ["09:30", "10:00", "10:30", "11:00", "11:20",
       "13:00", "13:30", "14:00", "14:30", "14:50"],
  35: ["09:30", "10:05", "10:40", "11:15",
       "13:00", "13:35", "14:10", "14:45"],
  40: ["09:30", "10:10", "10:50", "11:20",
       "13:00", "13:40", "14:20", "14:50"],
  45: ["09:30", "10:15", "11:00", "11:20",
       "13:00", "13:45", "14:30", "14:50"],
  50: ["09:30", "10:20", "11:10",
       "13:00", "13:50", "14:40"],
  55: ["09:30", "10:25", "11:20",
       "13:00", "13:55", "14:50"],
  60: ["09:30", "10:30", "11:20",
       "13:00", "14:00", "14:50"],
};

function isAnalysisChoice(value: number): value is (typeof ANALYSIS_INTERVAL_CHOICES)[number] {
  return (ANALYSIS_INTERVAL_CHOICES as readonly number[]).includes(value);
}

function generatePreview(intervalMinutes: number, kind: ScheduleKind) {
  if (kind === "market_analysis" && isAnalysisChoice(intervalMinutes)) {
    return ANALYSIS_TIMETABLE[intervalMinutes];
  }
  const result: string[] = [];

  const cadence = CADENCES[kind];
  const offset = cadence.startsOneIntervalLate ? intervalMinutes : 0;
  for (const { start, end } of cadence.sessions) {
    for (let minutes = start + offset; minutes <= end; minutes += intervalMinutes) {
      const hour = Math.floor(minutes / 60);
      const minute = minutes % 60;
      result.push(`${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
    }
  }

  return result;
}

const statusBadgeClass =
  "h-5 gap-1 rounded-full border px-2 text-[11px] font-medium leading-none [&>svg]:size-3";

/** Runtime-sync lifecycle badge keeps scheduler state visible beside the task name. */
function SyncStatusBadge({ schedule }: { schedule?: StrategySchedule | undefined }) {
  if (!schedule) {
    return (
      <Badge
        key="unsaved"
        variant="outline"
        className={cn(statusBadgeClass, "border-amber-500/30 bg-amber-500/10 text-amber-700")}
      >
        <CircleDashedIcon />
        未保存
      </Badge>
    );
  }
  if (schedule.sync_error) {
    return (
      <Badge
        key="sync-error"
        variant="outline"
        className={cn(statusBadgeClass, "border-destructive/30 bg-destructive/10 text-destructive")}
      >
        <TriangleAlertIcon />
        同步失败
      </Badge>
    );
  }
  if (schedule.revision > 0 && schedule.runtime_synced_revision === schedule.revision) {
    return (
      <Badge
        key="synced"
        variant="outline"
        className={cn(statusBadgeClass, "border-emerald-500/25 bg-emerald-500/10 text-emerald-700")}
      >
        <CheckCircle2Icon />
        已同步
      </Badge>
    );
  }
  return (
    <Badge
      key="pending"
      variant="outline"
      className={cn(statusBadgeClass, "border-sky-500/30 bg-sky-500/10 text-sky-700")}
    >
      <RefreshCwIcon />
      待同步
    </Badge>
  );
}

export type ScheduleSubmission =
  | { scheduleId?: undefined; payload: CreateSchedulePayload }
  | { scheduleId: number; payload: UpdateSchedulePayload };

type ScheduleSettingsCardsProps = {
  schedules: StrategySchedule[];
  savePending: boolean;
  writeDisabled: boolean;
  onSubmit: (args: ScheduleSubmission) => Promise<unknown>;
};

type ModeCardShellProps = {
  title: string;
  enabled: boolean;
  busy: boolean;
  schedule?: StrategySchedule | undefined;
  onToggle: (enabled: boolean) => void;
  children: ReactNode;
};

/** Shared card chrome: header with mode switch, collapsible settings body. */
function ModeCardShell({ title, enabled, busy, schedule, onToggle, children }: ModeCardShellProps) {
  return (
    <div className="border-border/60 bg-card/50 hover:border-border overflow-hidden rounded-xl border shadow-xs">
      <div className="flex items-center gap-3 px-4 py-3.5 sm:px-5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <span className="truncate text-sm font-semibold tracking-tight">{title}</span>
          {enabled && schedule !== undefined ? <SyncStatusBadge schedule={schedule} /> : null}
        </div>
        <Switch
          checked={enabled}
          disabled={busy}
          onCheckedChange={onToggle}
          aria-label={`${enabled ? "停用" : "启用"}${title}`}
          className="shrink-0"
        />
      </div>

      {schedule?.sync_error ? (
        <div className="border-destructive/20 bg-destructive/5 text-destructive border-t px-4 py-2 text-xs sm:px-5">
          调度同步失败：{schedule.sync_error}
        </div>
      ) : null}

      {enabled ? (
        <div className="border-border/60 flex flex-col gap-5 border-t px-4 pt-4 pb-4 sm:px-5">
          {children}
        </div>
      ) : null}
    </div>
  );
}

/** 定时任务：选择时/分后点击添加，按用户指定的时点每天运行。 */
function CustomTimeCard({
  kind,
  schedule,
  enabled,
  busy,
  onSubmit,
  onToggle,
}: {
  kind: ScheduleKind;
  schedule?: StrategySchedule | undefined;
  enabled: boolean;
  busy: boolean;
  onSubmit: ScheduleSettingsCardsProps["onSubmit"];
  onToggle: (enabled: boolean) => void;
}) {
  const cadence = CADENCES[kind];
  const title = `${cadence.label}定时任务`;
  const [times, setTimes] = useState<string[]>(() => schedule?.custom_schedule_times ?? []);
  const [hour, setHour] = useState("9");
  const [minute, setMinute] = useState("30");
  const [pickerError, setPickerError] = useState<string | null>(null);

  const addTime = () => {
    const hourValue = Number(hour);
    const minuteValue = Number(minute);
    if (!Number.isInteger(hourValue) || hourValue < 0 || hourValue > 23) {
      setPickerError("小时需为 0–23 的整数");
      return;
    }
    if (!Number.isInteger(minuteValue) || minuteValue < 0 || minuteValue > 59) {
      setPickerError("分钟需为 0–59 的整数");
      return;
    }
    setPickerError(null);
    const value = `${String(hourValue).padStart(2, "0")}:${String(minuteValue).padStart(2, "0")}`;
    if (times.includes(value)) {
      return;
    }
    if (times.length >= cadence.maxCustomTimes) {
      setPickerError(`最多添加 ${cadence.maxCustomTimes} 个时点`);
      return;
    }
    setTimes([...times, value].sort());
  };

  const removeTime = (value: string) => {
    setTimes((current) => current.filter((item) => item !== value));
  };

  const handleSave = async () => {
    if (times.length === 0) {
      return;
    }
    const payload = {
      enabled: true,
      task_type: kind,
      interval_minutes: schedule?.interval_minutes ?? cadence.defaultIntervalMinutes,
      schedule_times: times,
    };
    try {
      if (schedule) {
        await onSubmit({
          scheduleId: schedule.schedule_id,
          payload: { ...payload, expected_revision: schedule.revision },
        });
        return;
      }
      await onSubmit({ payload });
    } catch {
      // The page-level mutation owns error and conflict feedback.
    }
  };

  return (
    <ModeCardShell
      title={title}
      enabled={enabled}
      busy={busy}
      schedule={schedule}
      onToggle={onToggle}
    >
      <section className="flex flex-col gap-3" aria-label={`${title}调度规则`}>
        <SectionLabel icon={<SlidersHorizontalIcon className="size-3.5" />}>调度规则</SectionLabel>
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={`${kind}-custom-hour`} className="sr-only">
            时
          </Label>
          <Input
            id={`${kind}-custom-hour`}
            type="number"
            min={0}
            max={23}
            step={1}
            disabled={busy}
            value={hour}
            onChange={(event) => setHour(event.target.value)}
            className="h-8 w-20 text-center"
          />
          <span className="text-muted-foreground">:</span>
          <Label htmlFor={`${kind}-custom-minute`} className="sr-only">
            分
          </Label>
          <Input
            id={`${kind}-custom-minute`}
            type="number"
            min={0}
            max={59}
            step={1}
            disabled={busy}
            value={minute}
            onChange={(event) => setMinute(event.target.value)}
            className="h-8 w-20 text-center"
          />
          <Button variant="outline" size="sm" disabled={busy} onClick={addTime}>
            添加
          </Button>
        </div>
        {pickerError ? <p className="text-destructive text-xs">{pickerError}</p> : null}
        <p className="text-muted-foreground text-xs">
          输入时和分后点击“添加”，按指定时点每天（工作日）运行
        </p>
      </section>

      <section className="flex flex-col gap-3" aria-label={`${title}已选时点`}>
        <SectionLabel icon={<Clock3Icon className="size-3.5" />}>
          已选时点
          <span className="text-muted-foreground/80 font-normal tabular-nums">
            {times.length} 个
          </span>
        </SectionLabel>
        {times.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {times.map((time) => (
              <span
                key={time}
                className="border-border/60 bg-muted/40 text-foreground/80 inline-flex h-7 items-center gap-1 rounded-lg border px-2 text-xs font-medium tabular-nums"
              >
                {time}
                <button
                  type="button"
                  onClick={() => removeTime(time)}
                  aria-label={`移除 ${time}`}
                  className="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors"
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">请添加至少一个时点</p>
        )}
      </section>

      <div className="border-border/60 flex justify-end border-t pt-4">
        <Button disabled={busy || times.length === 0} onClick={() => void handleSave()}>
          {busy ? <Spinner className="size-4" /> : null}
          保存{title}设置
        </Button>
      </div>
    </ModeCardShell>
  );
}

/** 间隔任务：按间隔自动生成时点。 */
function IntervalCard({
  kind,
  schedule,
  enabled,
  busy,
  onSubmit,
  onToggle,
}: {
  kind: ScheduleKind;
  schedule?: StrategySchedule | undefined;
  enabled: boolean;
  busy: boolean;
  onSubmit: ScheduleSettingsCardsProps["onSubmit"];
  onToggle: (enabled: boolean) => void;
}) {
  const cadence = CADENCES[kind];
  const title = `${cadence.label}间隔任务`;
  const [intervalText, setIntervalText] = useState(() =>
    schedule?.interval_minutes
      ? String(schedule.interval_minutes)
      : String(cadence.defaultIntervalMinutes),
  );
  const interval = Math.max(
    cadence.minIntervalMinutes,
    Number(intervalText) || cadence.defaultIntervalMinutes,
  );
  const preview = useMemo(() => generatePreview(interval, kind), [interval, kind]);

  const handleSave = async () => {
    const payload = {
      enabled: true,
      task_type: kind,
      interval_minutes: interval,
      schedule_times: null,
    };
    try {
      if (schedule) {
        await onSubmit({
          scheduleId: schedule.schedule_id,
          payload: { ...payload, expected_revision: schedule.revision },
        });
        return;
      }
      await onSubmit({ payload });
    } catch {
      // The page-level mutation owns error and conflict feedback.
    }
  };

  return (
    <ModeCardShell
      title={title}
      enabled={enabled}
      busy={busy}
      schedule={schedule}
      onToggle={onToggle}
    >
      <section className="flex flex-col gap-3" aria-label={`${title}调度规则`}>
        <SectionLabel icon={<SlidersHorizontalIcon className="size-3.5" />}>调度规则</SectionLabel>
        <div className="flex items-center gap-2 text-sm">
          <span>每</span>
          <Label htmlFor={`${kind}-interval-minutes`} className="sr-only">
            {cadence.label}运行间隔（分钟）
          </Label>
          {kind === "market_analysis" ? (
            <Select
              value={intervalText}
              disabled={busy}
              onValueChange={(value) => setIntervalText(value)}
            >
              <SelectTrigger id={`${kind}-interval-minutes`} className="h-8 w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ANALYSIS_INTERVAL_CHOICES.map((choice) => (
                  <SelectItem key={choice} value={String(choice)}>
                    {choice}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              id={`${kind}-interval-minutes`}
              type="number"
              min={cadence.minIntervalMinutes}
              max={240}
              disabled={busy}
              value={intervalText}
              onChange={(event) => setIntervalText(event.target.value)}
              className="h-8 w-20 text-center"
            />
          )}
          <span>分钟运行一次</span>
        </div>
        <p className="text-muted-foreground text-xs">
          {kind === "market_analysis"
            ? "每档间隔对应一套固定时点：从开盘起按间隔铺，最晚 11:20 / 14:50 起跑（收盘前 10 分钟）"
            : `按间隔自动生成工作日盘中时点，每个时段从开盘后一个间隔起（间隔最小 ${cadence.minIntervalMinutes} 分钟）`}
        </p>
      </section>

      <section className="flex flex-col gap-3" aria-label={`${title}运行时点预览`}>
        <SectionLabel icon={<Clock3Icon className="size-3.5" />}>
          运行时点预览
          <span className="text-muted-foreground/80 font-normal tabular-nums">
            {preview.length} 个
          </span>
        </SectionLabel>
        <div className="flex flex-wrap gap-2">
          {preview.map((time) => (
            <span
              key={time}
              className="border-border/60 bg-muted/40 text-foreground/80 inline-flex h-7 items-center rounded-lg border px-2.5 text-xs font-medium tabular-nums"
            >
              {time}
            </span>
          ))}
        </div>
      </section>

      <div className="border-border/60 flex justify-end border-t pt-4">
        <Button disabled={busy} onClick={() => void handleSave()}>
          {busy ? <Spinner className="size-4" /> : null}
          保存{title}设置
        </Button>
      </div>
    </ModeCardShell>
  );
}

/** Two mutually exclusive schedule modes: custom times or interval-derived. */
function ScheduleModeSection({
  kind,
  schedule,
  savePending,
  writeDisabled,
  onSubmit,
  children,
}: {
  kind: ScheduleKind;
  schedule?: StrategySchedule | undefined;
  savePending: boolean;
  writeDisabled: boolean;
  onSubmit: ScheduleSettingsCardsProps["onSubmit"];
  children?: ReactNode;
}) {
  const cadence = CADENCES[kind];
  const busy = writeDisabled || savePending;
  const customActive = schedule
    ? schedule.enabled && Boolean(schedule.custom_schedule_times)
    : false;
  const intervalActive = schedule ? schedule.enabled && !schedule.custom_schedule_times : false;
  const [customOn, setCustomOn] = useState(customActive);
  const [intervalOn, setIntervalOn] = useState(intervalActive || !schedule);

  const toggleCustom = (on: boolean) => {
    if (on) {
      setIntervalOn(false);
    }
    setCustomOn(on);
  };
  const toggleInterval = (on: boolean) => {
    if (on) {
      setCustomOn(false);
    }
    setIntervalOn(on);
  };

  const handleDisable = async () => {
    if (!schedule) {
      return;
    }
    const payload = {
      enabled: false,
      task_type: kind,
      interval_minutes: schedule.interval_minutes,
      // Keep any custom trigger times so re-enabling restores the same plan.
      schedule_times: schedule.custom_schedule_times ?? null,
      expected_revision: schedule.revision,
    };
    try {
      await onSubmit({ scheduleId: schedule.schedule_id, payload });
    } catch {
      // The page-level mutation owns error and conflict feedback.
    }
  };

  const alreadyDisabled = schedule !== undefined && !schedule.enabled;

  return (
    <section className="flex flex-col gap-4" aria-label={`${cadence.label}任务`}>
      <h3 className="text-base font-semibold tracking-tight">{cadence.label}任务</h3>
      {children}
      <div className="flex flex-col gap-4">
        <CustomTimeCard
          kind={kind}
          schedule={schedule}
          enabled={customOn}
          busy={busy}
          onSubmit={onSubmit}
          onToggle={toggleCustom}
        />
        <IntervalCard
          kind={kind}
          schedule={schedule}
          enabled={intervalOn}
          busy={busy}
          onSubmit={onSubmit}
          onToggle={toggleInterval}
        />
      </div>

      {schedule && !customOn && !intervalOn ? (
        <div className="border-border/60 bg-card/50 flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 sm:px-5">
          {alreadyDisabled ? (
            <span className="text-muted-foreground text-sm">
              {cadence.label}任务已停用，不会自动运行
            </span>
          ) : (
            <>
              <span className="text-muted-foreground text-sm">
                当前未启用任何运行方式，{cadence.label}任务将不会自动运行
              </span>
              <Button variant="outline" disabled={busy} onClick={() => void handleDisable()}>
                停用{cadence.label}任务
              </Button>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}

export function ScheduleSettingsCards({
  schedules,
  savePending,
  writeDisabled,
  onSubmit,
}: ScheduleSettingsCardsProps) {
  const scheduleMap = useMemo(
    () => Object.fromEntries(schedules.map((schedule) => [schedule.task_type, schedule])),
    [schedules],
  );

  return (
    <div className="flex flex-col gap-8">
      <ScheduleModeSection
        kind="market_analysis"
        key={`market-${scheduleMap.market_analysis?.updated_at ?? "new"}`}
        schedule={scheduleMap.market_analysis}
        savePending={savePending}
        writeDisabled={writeDisabled}
        onSubmit={onSubmit}
      />
      <ScheduleModeSection
        kind="order_watch"
        key={`watch-${scheduleMap.order_watch?.updated_at ?? "new"}`}
        schedule={scheduleMap.order_watch}
        savePending={savePending}
        writeDisabled={writeDisabled}
        onSubmit={onSubmit}
      >
        {/* Enabling this is the first time a watch ever runs. Everything else
            merged so far changed nothing on the schedule; this row does. */}
        <p className="text-muted-foreground text-xs">
          盯盘按操盘写下的挂单处置清单逐笔核对，只撤单、不研究。启用后会立刻按此节奏运行——
          请先在「阶段设置」里为盯盘阶段选好模型（建议 flash 类型、低档位），否则每次运行都会失败。
        </p>
      </ScheduleModeSection>
    </div>
  );
}
