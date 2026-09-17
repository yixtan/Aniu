import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ScaleIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { findingKeys } from "@/features/findings/query-keys";
import { getRunEvaluation, raiseOpenFinding, requestRunEvaluation } from "@/lib/api";
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

  const [finding, setFinding] = useState("");
  const [resolutionTest, setResolutionTest] = useState("");
  const raise = useMutation({
    mutationFn: () =>
      raiseOpenFinding({
        finding,
        resolution_test: resolutionTest,
        evaluation_id: evaluationQuery.data?.evaluation_id ?? null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: findingKeys.all });
      setFinding("");
      setResolutionTest("");
      toast.success("已立为未结议题，下一次操盘起必须逐条表态");
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
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
        {evaluation?.status === "COMPLETED" ? (
          <section className="border-border/60 space-y-2 border-t pt-3">
            <h3 className="text-xs font-semibold">立为未结议题</h3>
            <Field>
              <FieldLabel htmlFor="finding">发现</FieldLabel>
              <Textarea
                id="finding"
                rows={2}
                value={finding}
                placeholder="从上面的问答里挑一条值得追究的，写成一句话"
                onChange={(event) => setFinding(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="resolution-test">了结条件</FieldLabel>
              <Textarea
                id="resolution-test"
                rows={2}
                value={resolutionTest}
                placeholder="什么证据或改动会让这条议题了结"
                onChange={(event) => setResolutionTest(event.target.value)}
              />
              <FieldDescription>
                必填。说不出怎样才算了结的议题，会在此后每一次运行里被回答而永远留在列表上。
              </FieldDescription>
            </Field>
            <Button
              size="sm"
              variant="outline"
              disabled={
                raise.isPending || finding.trim() === "" || resolutionTest.trim() === ""
              }
              onClick={() => raise.mutate()}
            >
              立项
            </Button>
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
