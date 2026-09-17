import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ScaleIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getRunEvaluation, requestRunEvaluation } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";

/** Poll only while something is actually being written. */
const IN_FLIGHT = new Set(["PENDING", "RUNNING"]);

export function EvaluationCard({ runId }: { runId: number }) {
  const queryClient = useQueryClient();
  const queryKey = ["run-evaluation", runId] as const;
  const evaluationQuery = useQuery({
    queryKey,
    queryFn: () => getRunEvaluation(runId),
    refetchInterval: (query) =>
      IN_FLIGHT.has(query.state.data?.status ?? "") ? 3_000 : false,
  });
  const request = useMutation({
    mutationFn: () => requestRunEvaluation(runId),
    onSuccess: (created) => {
      queryClient.setQueryData(queryKey, created);
    },
  });

  const evaluation = evaluationQuery.data ?? null;
  const running = evaluation !== null && IN_FLIGHT.has(evaluation.status);
  const busy = request.isPending || running;

  return (
    <Card className="border-border/75 bg-card/90 gap-2 py-4 shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-0">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <ScaleIcon aria-hidden className="text-muted-foreground size-4" />
          独立评估
        </CardTitle>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => request.mutate()}>
          {busy ? "评估中…" : evaluation ? "重新评估" : "开始评估"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground text-xs">
          一位不知道本系统规则的第三方，对照账户的委托成交记录审查这次运行，并由本次运行作答。
          它读不到记忆库和全局提示词，也下不了单。
        </p>
        {request.isError ? (
          <p className="text-destructive text-xs">{getErrorMessage(request.error)}</p>
        ) : null}
        {evaluation?.status === "FAILED" ? (
          <p className="text-destructive text-xs">
            {evaluation.failure_reason ?? "评估失败，但没有记录原因。"}
          </p>
        ) : null}
        {running ? (
          <p className="text-muted-foreground text-xs tabular-nums">
            正在生成提问与回答，通常一到两分钟。
          </p>
        ) : null}
        {evaluation?.questions ? (
          <section className="space-y-1">
            <h3 className="text-xs font-semibold">提问</h3>
            <div className="text-foreground/90 text-xs leading-relaxed whitespace-pre-wrap">
              {evaluation.questions}
            </div>
          </section>
        ) : null}
        {evaluation?.answers ? (
          <section className="space-y-1">
            <h3 className="text-xs font-semibold">回答</h3>
            <div className="text-foreground/90 text-xs leading-relaxed whitespace-pre-wrap">
              {evaluation.answers}
            </div>
          </section>
        ) : null}
        {evaluation?.status === "COMPLETED" && evaluation.total_tokens > 0 ? (
          <p className="text-muted-foreground text-xs tabular-nums">
            token {evaluation.total_tokens.toLocaleString()}
            {evaluation.cached_tokens > 0
              ? `（缓存命中 ${evaluation.cached_tokens.toLocaleString()}）`
              : ""}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
