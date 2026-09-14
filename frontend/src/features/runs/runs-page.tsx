import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { deleteRun, listRunDays, listRuns, listSchedules } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";
import { RunWorkbenchPanel } from "@/features/runs/components/run-workbench";
import { RunDateNav } from "@/features/runs/components/run-date-nav";
import { RunTimetable } from "@/features/runs/components/run-timetable";
import { RunStartButton } from "@/features/runs/components/run-start-button";
import {
  buildTimetable,
  isOrderWatchTask,
  runDayOf,
  type TimetableSlot,
} from "@/features/runs/timetable";
import { Button } from "@/components/ui/button";
import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import type { RunSummary } from "@/lib/api-types";
import { runKeys } from "@/features/runs/query-keys";

/** One day holds about ninety runs, so a day is fetched whole rather than paged. */
const RUNS_PER_DAY = 200;

export function RunsPage() {
  const queryClient = useQueryClient();
  const [pickedDay, setPickedDay] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [stoppingRunId, setStoppingRunId] = useState<number | null>(null);
  const [now, setNow] = useState(() => new Date());

  const daysQuery = useQuery({
    queryKey: runKeys.days(),
    queryFn: () => listRunDays(),
    refetchInterval: 60_000,
  });
  const days = useMemo(() => daysQuery.data ?? [], [daysQuery.data]);
  const today = runDayOf(now);
  const day = pickedDay ?? days[0]?.day ?? today;
  const isToday = day === today;

  const runsQuery = useQuery({
    queryKey: runKeys.day(day),
    queryFn: () => listRuns(RUNS_PER_DAY, 0, day),
    // A finished day cannot change; today keeps discovering scheduled runs.
    refetchInterval: isToday ? 15_000 : false,
  });
  // Kept separate from the day being viewed: whether a run is in flight decides
  // if a manual run is allowed, and that must stay true while reading history.
  const activeQuery = useQuery({
    queryKey: runKeys.active(),
    queryFn: () => listRuns(5, 0),
    refetchInterval: 15_000,
  });
  // Only today has planned times: the schedule derives them from the current
  // settings, so an older day would be drawn against a timetable it never ran.
  const schedulesQuery = useQuery({
    queryKey: ["schedules", "list"],
    queryFn: listSchedules,
    enabled: isToday,
  });

  const runs = useMemo(() => runsQuery.data ?? [], [runsQuery.data]);
  const runningRun = useMemo(
    () => (activeQuery.data ?? []).find((run) => run.status === "RUNNING") ?? null,
    [activeQuery.data],
  );
  const plannedTimes = useMemo(() => {
    const of = (taskType: string) => {
      const schedule = (schedulesQuery.data ?? []).find(
        (item) => item.task_type === taskType && item.enabled,
      );
      return isToday ? (schedule?.schedule_times ?? []) : [];
    };
    return { analysis: of("market_analysis"), watch: of("order_watch") };
  }, [isToday, schedulesQuery.data]);

  const analysisSlots = useMemo(
    () =>
      buildTimetable({
        plannedTimes: plannedTimes.analysis,
        runs: runs.filter((run) => !isOrderWatchTask(run.task_id)),
        now,
      }),
    [now, plannedTimes.analysis, runs],
  );
  const watchSlots = useMemo(
    () =>
      buildTimetable({
        plannedTimes: plannedTimes.watch,
        runs: runs.filter((run) => isOrderWatchTask(run.task_id)),
        now,
      }),
    [now, plannedTimes.watch, runs],
  );

  const hasRunningRun = runningRun !== null;

  useEffect(() => {
    if (!hasRunningRun) return;
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, [hasRunningRun]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );
  // A run id from the day you just left names nothing here, so the panel would
  // sit empty until something is clicked. Fall back to the day's last run.
  const shownRunId = selectedRun?.run_id ?? runs[0]?.run_id ?? null;
  const effectiveStoppingRunId =
    stoppingRunId !== null && runningRun?.run_id === stoppingRunId ? stoppingRunId : null;

  const deleteMutation = useMutation({
    mutationFn: deleteRun,
    onSuccess: async (_, deletedRunId) => {
      toast.success("运行记录已删除");
      setSelectedRunId((current) => (current === deletedRunId ? null : current));
      queryClient.removeQueries({ queryKey: runKeys.detail(deletedRunId) });
      await queryClient.invalidateQueries({ queryKey: runKeys.all });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const selectSlot = (slot: TimetableSlot) => {
    if (slot.run !== null) setSelectedRunId(slot.run.run_id);
  };

  if (daysQuery.isPending) {
    return <QueryLoadingState label="正在加载运行日程…" />;
  }

  if (daysQuery.isError && !daysQuery.data) {
    return (
      <QueryErrorState
        error={daysQuery.error}
        title="运行日程加载失败"
        onRetry={() => void daysQuery.refetch()}
      />
    );
  }

  return (
    <div className="text-foreground flex min-h-0 flex-1 flex-col space-y-4 font-sans">
      {runsQuery.isError ? (
        <div
          role="alert"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800"
        >
          这一天的运行记录加载失败：{getErrorMessage(runsQuery.error)}
        </div>
      ) : null}

      <div className="mb-2 flex items-center justify-between space-y-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">任务运行</h1>
          <p className="text-muted-foreground text-sm">
            {isToday ? "今日日程" : `${day} 的运行记录`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <RunStartButton
            triggerLabel="手动运行"
            disabled={hasRunningRun}
            runningRunId={runningRun?.run_id ?? null}
            isStopping={
              effectiveStoppingRunId !== null && effectiveStoppingRunId === runningRun?.run_id
            }
            onStartRequested={() => setStoppingRunId(null)}
            onStopRequested={setStoppingRunId}
            onStopFailed={(runId) => {
              setStoppingRunId((current) => (current === runId ? null : current));
            }}
            onStarted={(run: RunSummary) => {
              setStoppingRunId(null);
              setPickedDay(today);
              setSelectedRunId(run.run_id);
              void queryClient.invalidateQueries({ queryKey: runKeys.all });
            }}
          />
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="destructive"
                disabled={
                  selectedRun === null ||
                  selectedRun.status === "RUNNING" ||
                  deleteMutation.isPending
                }
              >
                <Trash2Icon data-icon="inline-start" />
                删除记录
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent size="sm">
              <AlertDialogHeader>
                <AlertDialogTitle>删除运行记录</AlertDialogTitle>
                <AlertDialogDescription>
                  删除后将一并移除该次运行的时间线事件，无法恢复
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  onClick={() => {
                    if (selectedRun !== null) deleteMutation.mutate(selectedRun.run_id);
                  }}
                >
                  确认删除
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      <Card className="border-border/75 bg-card/90 gap-0 py-0 shadow-sm">
        <CardContent className="px-3 py-3">
          {days.length === 0 ? (
            <Empty className="min-h-0 py-4">
              <EmptyHeader>
                <EmptyTitle>暂无运行记录</EmptyTitle>
                <EmptyDescription>
                  等待定时任务触发，或直接发起一次手动运行
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <RunDateNav days={days} selectedDay={day} onSelectDay={setPickedDay} />
          )}
        </CardContent>
      </Card>

      <Card className="border-border/75 bg-card/90 gap-0 py-0 shadow-sm">
        <CardContent className="space-y-5 px-4 py-4">
          <RunTimetable
            title="操盘"
            hint={isToday ? "按当前设置排定的时点" : "当天实际发生的运行"}
            slots={analysisSlots}
            selectedRunId={shownRunId}
            onSelectSlot={selectSlot}
          />
          <RunTimetable
            title="盯盘"
            hint={
              isToday
                ? "和操盘撞点时会跳过，虚线即为没有产生运行的时点"
                : "当天实际发生的运行"
            }
            slots={watchSlots}
            selectedRunId={shownRunId}
            onSelectSlot={selectSlot}
          />
        </CardContent>
      </Card>

      <div className="flex min-h-0 flex-1 flex-col">
        <RunWorkbenchPanel
          runId={shownRunId}
          now={now}
          isStopping={effectiveStoppingRunId === shownRunId}
        />
      </div>
    </div>
  );
}
