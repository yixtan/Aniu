import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckIcon, ChevronRightIcon, CircleAlertIcon, CopyIcon, MailIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { emailRunReport } from "@/lib/api";
import { formatRunDuration, getErrorMessage } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { RunDetail } from "@/lib/api-types";

import { StreamingContent } from "../streaming";
import { StageNode } from "./stage-node";

/** Whether the browser exposes the Clipboard API at all.
 *
 * It only exists in a secure context, so an installation served over plain
 * HTTP — a public host without TLS, or a LAN address — has none. The button is
 * hidden in that case rather than offered and then failing.
 */
function clipboardAvailable() {
  return typeof navigator !== "undefined" && Boolean(navigator.clipboard);
}

/** Mail this run's report to the configured address.
 *
 * Always offered, unlike the copy button: a missing configuration is something
 * the operator can fix, and the error says where to fix it.
 */
function EmailReportButton({ runId }: { runId: number }) {
  const sendMutation = useMutation({
    mutationFn: () => emailRunReport(runId),
    onSuccess: (result) => {
      if (result.delivered) {
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-7 gap-1.5 px-2 text-xs"
      disabled={sendMutation.isPending}
      onClick={() => sendMutation.mutate()}
    >
      <MailIcon className="size-3.5" />
      {sendMutation.isPending ? "发送中…" : "发送到邮箱"}
    </Button>
  );
}

/** Copy the report's Markdown source so it can be pasted into an editor. */
function CopyReportButton({ markdown }: { markdown: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
    } catch (error) {
      // A secure context can still refuse the write, e.g. when the document is
      // not focused or the permission was denied.
      toast.error(error instanceof Error ? error.message : "复制失败");
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-7 gap-1.5 px-2 text-xs"
      onClick={() => void copy()}
    >
      {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
      {copied ? "已复制" : "复制 Markdown"}
    </Button>
  );
}

/**
 * Compact stage process summary followed by the terminal report or failure reason.
 * The parent owns scrolling and the ticking clock.
 */

export function StageTimeline({
  run,
  now,
  liveStepDeltaByStepId,
  isStopping = false,
}: {
  run: RunDetail;
  now: Date;
  liveStepDeltaByStepId: Record<string, string>;
  isStopping?: boolean;
}) {
  const trace = run.trace;
  const stages = trace.stages;
  const isFailed = run.status === "FAILED";
  const recordedFailureReason = run.failure_reason?.trim() || null;
  const shouldExpandFailedStages = isFailed && recordedFailureReason !== null;
  const [stagesExpandedOverride, setStagesExpandedOverride] = useState<boolean | null>(null);
  const stagesExpanded = stagesExpandedOverride ?? shouldExpandFailedStages;
  const stageListId = `run-stage-list-${run.run_id}`;
  const toggleStages = () => {
    setStagesExpandedOverride((override) => !(override ?? shouldExpandFailedStages));
  };
  if (stages.length === 0 && !isFailed) {
    return (
      <Empty className="min-h-[220px] justify-center">
        <EmptyHeader>
          <EmptyTitle>暂无执行阶段</EmptyTitle>
        </EmptyHeader>
      </Empty>
    );
  }

  const totalDuration = formatRunDuration(run.started_at, run.completed_at, now);
  const runStage = stages.find((stage) => stage.key === "run");
  const summaryStage = stages.find((stage) => stage.key === "summary");
  const runStatusLabel =
    isStopping && run.status === "RUNNING"
      ? "停止中"
      : run.status === "ABORTED"
        ? "已中止"
        : run.status === "FAILED" || runStage?.status === "failed"
          ? "执行失败"
          : run.status === "COMPLETED" || runStage?.status === "completed"
            ? "执行完成"
            : "执行中";
  const finalReportSteps = summaryStage?.steps.filter((step) => step.type === "result") ?? [];
  // The run summary is the authoritative final report; the summary stage result
  // is only a fallback while a terminal snapshot is settling.
  const finalReportContent =
    run.summary?.trim() ||
    finalReportSteps
      .map((step) => step.content?.trim() || step.summary?.trim() || "")
      .filter(Boolean)
      .join("\n\n");
  const showFinalReport =
    (summaryStage?.status === "completed" || summaryStage?.status === "degraded") &&
    finalReportContent.length > 0;
  // The Summary stage rewrites the report as HTML for display, so the Markdown
  // an editor wants is the Run stage's own result, not what is on screen.
  const markdownReport =
    runStage?.steps
      .filter((step) => step.type === "result")
      .map((step) => step.content?.trim() || "")
      .filter(Boolean)
      .join("\n\n") || (run.summary_render_mode === "html" ? "" : finalReportContent);
  const failureReason = recordedFailureReason || "任务执行失败，但没有记录具体失败原因。";

  return (
    <>
      <div className="text-muted-foreground flex flex-wrap items-center gap-1 px-2 pb-1.5 text-[12px] leading-5">
        <button
          type="button"
          aria-label={stagesExpanded ? "收起阶段" : "展开阶段"}
          aria-controls={stageListId}
          aria-expanded={stagesExpanded}
          title={stagesExpanded ? "收起阶段" : "展开阶段"}
          onClick={toggleStages}
          className="hover:bg-muted/60 focus-visible:ring-ring/50 -ms-1 inline-flex size-5 shrink-0 items-center justify-center rounded-sm transition-colors outline-none focus-visible:ring-[3px]"
        >
          <ChevronRightIcon
            aria-hidden
            className={cn(
              "size-3.5 transition-transform duration-150",
              stagesExpanded && "rotate-90",
            )}
          />
        </button>
        <button
          type="button"
          aria-controls={stageListId}
          aria-expanded={stagesExpanded}
          title={stagesExpanded ? "收起阶段" : "展开阶段"}
          onClick={toggleStages}
          className="focus-visible:ring-ring/50 inline-flex min-w-0 flex-wrap items-center gap-1 rounded-sm text-start outline-none focus-visible:ring-[3px]"
        >
          <span className="text-foreground font-semibold">运行摘要</span>
          <span aria-hidden>·</span>
          <span className="font-medium">{runStatusLabel}</span>
          <span aria-hidden>·</span>
          <span className="font-medium tabular-nums">工具{run.tool_calls_count}次</span>
          <span aria-hidden>·</span>
          <span className="font-medium tabular-nums">交易{run.trade_count}次</span>
          <span aria-hidden>·</span>
          <span className="font-medium tabular-nums">总耗时{totalDuration}</span>
        </button>
      </div>

      {stagesExpanded ? (
        <ol id={stageListId} className="flex flex-col gap-0 py-0">
          {stages.map((stage) => (
            <StageNode
              key={stage.stage_id}
              run={run}
              stage={stage}
              now={now}
              liveStepDeltaByStepId={liveStepDeltaByStepId}
              isStopping={isStopping}
            />
          ))}
        </ol>
      ) : null}

      {isFailed ? (
        <section className="px-2 pt-4 pb-3">
          <h2 className="text-foreground mb-3 font-sans text-base font-semibold tracking-[-0.01em]">
            最终运行报告
          </h2>
          <div
            role="alert"
            aria-label="失败原因"
            className="border-destructive/70 bg-destructive/[0.04] flex items-start gap-2 border-s-2 px-3 py-2.5"
          >
            <CircleAlertIcon aria-hidden className="text-destructive mt-0.5 size-4 shrink-0" />
            <div className="min-w-0">
              <p className="text-destructive text-[12px] font-medium">失败原因</p>
              <p className="text-foreground mt-1 max-h-48 overflow-auto text-[13px] leading-5 break-words whitespace-pre-wrap">
                {failureReason}
              </p>
            </div>
          </div>
        </section>
      ) : showFinalReport ? (
        <section className="px-2 pt-4 pb-3">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-foreground font-sans text-base font-semibold tracking-[-0.01em]">
              最终运行报告
            </h2>
            <div className="flex shrink-0 items-center gap-2">
              {markdownReport && clipboardAvailable() ? (
                <CopyReportButton markdown={markdownReport} />
              ) : null}
              <EmailReportButton runId={run.run_id} />
            </div>
          </div>
          <StreamingContent
            content={finalReportContent}
            streaming={false}
            scrollable={false}
            renderMode={run.summary_render_mode ?? "markdown"}
          />
        </section>
      ) : null}
    </>
  );
}
