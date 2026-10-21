import { CircleAlertIcon } from "lucide-react";

import type { RunDetail } from "@/lib/api-types";

export function SummaryStatusNotice({ run }: { run: RunDetail }) {
  const stages = run.trace.stages;
  const summary = stages.find((stage) => stage.key === "summary");
  const isAnalysis = summary !== undefined || stages.some((stage) => stage.key === "run");
  if (
    !isAnalysis ||
    run.status !== "COMPLETED" ||
    run.summary_render_mode !== "markdown" ||
    !run.summary?.trim()
  ) {
    return null;
  }
  const reason = summary?.steps
    .find((step) => step.step_id === "markdown_fallback")
    ?.summary?.trim();

  return (
    <div
      role="status"
      aria-label="总结降级提示"
      className="mx-2 my-2 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-sm"
    >
      <CircleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-amber-700" />
      <div className="min-w-0 space-y-1">
        <p className="font-medium">HTML 总结已降级</p>
        <p className="text-muted-foreground">运行已完成，已保留 Markdown 报告。</p>
        {reason ? (
          <details>
            <summary className="cursor-pointer text-xs">查看降级原因</summary>
            <p className="mt-1 max-h-40 overflow-auto text-xs break-words whitespace-pre-wrap">
              {reason}
            </p>
          </details>
        ) : (
          <p className="text-muted-foreground text-xs">这次运行未记录详细的降级原因。</p>
        )}
      </div>
    </div>
  );
}
