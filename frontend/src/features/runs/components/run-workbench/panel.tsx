import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { getRunDetail } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";
import { useRunSnapshotStream } from "@/features/runs/hooks/use-run-snapshot-stream";
import type { RunDetail } from "@/lib/api-types";
import { EvaluationCard } from "@/features/runs/components/run-workbench/evaluation-card";
import { StageTimeline } from "@/features/runs/components/run-workbench/timeline/stage-timeline";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { runKeys } from "@/features/runs/query-keys";

export function RunWorkbenchPanel({
  runId,
  now,
  isStopping = false,
}: {
  runId: number | null;
  now: Date;
  isStopping?: boolean;
}) {
  if (runId === null) {
    return (
      <Card className="h-full">
        <CardContent className="flex h-full items-center justify-center">
          <Empty className="min-h-[220px] justify-center">
            <EmptyHeader>
              <EmptyTitle>请选择一条运行记录</EmptyTitle>
            </EmptyHeader>
          </Empty>
        </CardContent>
      </Card>
    );
  }

  return <SelectedRunWorkbench runId={runId} now={now} isStopping={isStopping} />;
}

function SelectedRunWorkbench({
  runId,
  now,
  isStopping,
}: {
  runId: number;
  now: Date;
  isStopping: boolean;
}) {
  const detailQuery = useQuery({
    queryKey: runKeys.detail(runId),
    queryFn: () => getRunDetail(runId),
    refetchInterval: (query) => (query.state.data?.status === "RUNNING" ? 2_000 : false),
  });

  if (detailQuery.error) {
    return (
      <Card className="h-full">
        <CardContent className="flex h-full items-center justify-center">
          <Empty>
            <EmptyHeader>
              <EmptyTitle>{getErrorMessage(detailQuery.error)}</EmptyTitle>
            </EmptyHeader>
          </Empty>
        </CardContent>
      </Card>
    );
  }

  if (detailQuery.isLoading || !detailQuery.data) {
    return (
      <Card className="h-full">
        <CardHeader>
          <CardDescription>正在加载运行 {runId} 的详情</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <RunWorkbenchContent
      key={detailQuery.data.run_id}
      initialSnapshot={detailQuery.data}
      now={now}
      isStopping={isStopping}
    />
  );
}

function RunWorkbenchContent({
  initialSnapshot,
  now,
  isStopping,
}: {
  initialSnapshot: RunDetail;
  now: Date;
  isStopping: boolean;
}) {
  const { snapshot, liveStepDeltaByStepId } = useRunSnapshotStream(
    initialSnapshot.status === "RUNNING" ? initialSnapshot.run_id : undefined,
    initialSnapshot,
  );
  const isLiveRun = snapshot.status === "RUNNING" && !isStopping;
  const trace = snapshot.trace;

  // The live stage (if any) — used to drive tail-follow behavior.
  const liveStage = useMemo(
    () =>
      trace.stages.find(
        (stage) => stage.stage_id === trace.current_stage_id && stage.status === "running",
      ) ?? null,
    [trace.stages, trace.current_stage_id],
  );
  const isLive = isLiveRun && liveStage != null;

  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const previousStageIdRef = useRef<string | null>(liveStage?.stage_id ?? null);
  const previousLiveRef = useRef(isLive);

  // Track whether the user is near the bottom (tail-follow gate).
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickRef.current = distance < 48;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Reset on stage transition or when live settles.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const stageChanged = previousStageIdRef.current !== (liveStage?.stage_id ?? null);
    const justSettled = previousLiveRef.current && !isLive;
    if (stageChanged || justSettled) {
      stickRef.current = isLive;
      el.scrollTop = isLive ? el.scrollHeight : 0;
    }
    previousStageIdRef.current = liveStage?.stage_id ?? null;
    previousLiveRef.current = isLive;
  }, [liveStage?.stage_id, isLive]);

  // Tail-follow while live streaming.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && isLive && stickRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [trace.stages, liveStepDeltaByStepId, isLive, now]);

  return (
    /* `min-h-full`, not `h-full`: the column has to be allowed to outgrow the
       panel. With a fixed height the two cards fight over it, and the report
       loses — it is the only one that may shrink below its content, so a
       finished evaluation (questions, answers, three drafts) squeezed it to
       the 2px of its own borders and the report read as blank. */
    <div className="flex min-h-full flex-col gap-4">
      {/* A floor rather than a share, for the same reason the panel itself has
          one. `flex-1` still hands it the whole panel when the evaluation card
          is small or absent. */}
      <Card className="border-border/75 bg-card/90 flex min-h-[24rem] flex-1 flex-col overflow-hidden shadow-sm">
        <CardContent className="min-h-0 flex-1 px-4 py-3">
          <div ref={scrollerRef} className="h-full min-h-0 overflow-y-auto">
            <StageTimeline
              run={snapshot}
              now={now}
              liveStepDeltaByStepId={liveStepDeltaByStepId}
              isStopping={isStopping}
            />
          </div>
        </CardContent>
      </Card>
      {isLiveRun ? null : <EvaluationCard runId={snapshot.run_id} />}
    </div>
  );
}
