import type { RunDetail } from "@/lib/api-types";
import { StreamingContent } from "./streaming";

export function ValuationCancellationsNotice({ run }: { run: RunDetail }) {
  const step = run.trace.stages
    .flatMap((stage) => stage.steps)
    .find((item) => item.step_id === "system_valuation_cancellations");
  if (!step?.content) return null;
  return (
    <section aria-label="系统挂单估值处置" className="border-border mb-4 rounded-lg border p-3 text-sm">
      <StreamingContent content={step.content} streaming={false} scrollable={false} variant="report" />
    </section>
  );
}
