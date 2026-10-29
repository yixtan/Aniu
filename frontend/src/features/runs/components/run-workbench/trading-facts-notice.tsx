import type { RunDetail } from "@/lib/api-types";
import { StreamingContent } from "./streaming";

export function TradingFactsNotice({ run }: { run: RunDetail }) {
  const stage = run.trace.stages.find((s) => s.key === "run");
  if (!stage) return null;
  const step = stage.steps.find((s) => s.step_id === "system_trading_facts");
  if (!step && run.status === "RUNNING") return null;

  return (
    <section aria-label="系统交易事实" className="border-border mb-4 rounded-lg border p-3 text-sm">
      {step?.content ? (
        <StreamingContent
          content={step.content}
          streaming={false}
          scrollable={false}
          variant="report"
        />
      ) : (
        <p className="text-muted-foreground">
          本次没有系统交易事实记录。报告中的审核状态和潜在敞口为模型文字，不等同于系统确认。
        </p>
      )}
    </section>
  );
}
