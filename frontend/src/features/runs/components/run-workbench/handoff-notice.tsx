import type { RunDetail } from "@/lib/api-types";

export function HandoffNotice({ run }: { run: RunDetail }) {
  const stage = run.trace.stages.find((s) => s.key === "run");
  if (!stage) return null;
  const step = stage.steps.find((s) => s.step_id === "order_handoff_check");
  if (!step && run.status === "RUNNING") return null;
  const complete = step?.status === "completed";

  return (
    <section
      aria-label="挂单交接检查"
      className={`mb-4 rounded-lg border p-3 text-sm ${complete ? "border-border" : "border-amber-500/40 bg-amber-500/5"}`}
    >
      <p className="font-medium">{step?.summary ?? "交接待核对：本次没有系统交接检查记录。"}</p>
      {step?.content && <p className="mt-2 whitespace-pre-wrap">{step.content}</p>}
      {!complete && (
        <p className="text-muted-foreground mt-2">
          报告生成成功不等于挂单交接完成。请核对遗漏委托；系统不会自动撤单或重跑交易。
        </p>
      )}
    </section>
  );
}
