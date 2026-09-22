import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ScaleIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { RecordId } from "@/components/record-id";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { findingKeys } from "@/features/findings/query-keys";
import { StreamingContent } from "@/features/runs/components/run-workbench/streaming";
import { getRunEvaluation, raiseOpenFinding, requestRunEvaluation } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";

/** Poll only while something is actually being written. */
const IN_FLIGHT = new Set(["PENDING", "RUNNING"]);

type Draft = { finding: string; resolutionTest: string };

export function EvaluationCard({ runId }: { runId: number }) {
  const queryClient = useQueryClient();
  const queryKey = ["run-evaluation", runId] as const;
  const evaluationQuery = useQuery({
    queryKey,
    queryFn: () => getRunEvaluation(runId),
    refetchInterval: (query) =>
      IN_FLIGHT.has(query.state.data?.status ?? "") ? 3_000 : false,
  });
  // Empty is the ordinary case: the button on its own still runs a review
  // where the reviewer picks every angle.
  const [question, setQuestion] = useState("");
  const request = useMutation({
    mutationFn: () => requestRunEvaluation(runId, question),
    onSuccess: (created) => {
      queryClient.setQueryData(queryKey, created);
      setQuestion("");
    },
  });

  // Null while nothing is being raised, so a long answer is not followed by
  // two empty boxes the reader has to scroll past to reach anything else.
  const [draft, setDraft] = useState<Draft | null>(null);
  // The argument is some five thousand characters. It is the evidence and
  // it stays, but it is not what you open the card to find out.
  const [showArgument, setShowArgument] = useState(false);
  const raise = useMutation({
    mutationFn: () =>
      raiseOpenFinding({
        finding: draft?.finding ?? "",
        resolution_test: draft?.resolutionTest ?? "",
        evaluation_id: evaluationQuery.data?.evaluation_id ?? null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: findingKeys.all });
      setDraft(null);
      toast.success("已立为未结议题，下一次操盘起必须逐条表态");
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  const evaluation = evaluationQuery.data ?? null;
  const running = evaluation !== null && IN_FLIGHT.has(evaluation.status);
  const busy = request.isPending || running;

  return (
    <Card className="border-border/75 bg-card/90 shrink-0 gap-2 py-4 shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-0">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <ScaleIcon aria-hidden className="text-muted-foreground size-4" />
          独立评估
          {/* Only once there is one to name. A review that has not been asked
              for yet has no id, and a blank badge would be chrome. */}
          {evaluation ? (
            <RecordId id={evaluation.evaluation_id} label="评估" className="text-xs" />
          ) : null}
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
        {evaluation === null || evaluation.status === "COMPLETED" ? (
          <div className="space-y-1">
            <label
              className="text-foreground block text-xs font-medium"
              htmlFor={`operator-question-${runId}`}
            >
              {evaluation?.operator_question ? "想再问一个？" : "你有想问的吗？"}（可以空着）
            </label>
            <Textarea
              id={`operator-question-${runId}`}
              rows={2}
              value={question}
              disabled={busy}
              placeholder="用大白话写就行，比如「今天行情已经变了，为什么仓位还保持 20%？」"
              onChange={(event) => setQuestion(event.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              写了的话，它会被排在第一个问题，而且评估会先对着记录把它问准。
              空着就由评估自己决定问什么。
            </p>
          </div>
        ) : null}

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
        {evaluation?.digest || evaluation?.operator_question ? (
          <section className="border-border/60 bg-muted/40 space-y-2 rounded-md border p-3">
            <h3 className="text-xs font-semibold">先看这段</h3>
            {/* Joined to the lead rather than left above it: the lead now
                opens by answering this, and the two read as one thing. */}
            {evaluation.operator_question ? (
              <p className="border-border/60 border-s ps-2 text-xs leading-relaxed">
                <span className="text-foreground font-medium">你问的是：</span>
                <span className="text-muted-foreground">
                  {evaluation.operator_question}
                </span>
              </p>
            ) : null}
            {evaluation.digest ? (
              <p className="text-xs leading-relaxed">{evaluation.digest}</p>
            ) : null}
            <p className="text-muted-foreground text-xs">
              这段只是指路，结论还得看下面的原文。
            </p>
          </section>
        ) : null}
        {evaluation?.questions || evaluation?.answers ? (
          <div className="space-y-2">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={() => setShowArgument((open) => !open)}
            >
              {showArgument ? "收起完整问答" : "看完整问答"}
            </Button>
            {/* Both halves answer in Markdown — headings, bold, tables. Rendered
                as plain text the page filled up with literal ## and **. */}
            {showArgument && evaluation.questions ? (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold">提问</h3>
                <StreamingContent
                  content={evaluation.questions}
                  streaming={false}
                  scrollable={false}
                  variant="process"
                />
              </section>
            ) : null}
            {showArgument && evaluation.answers ? (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold">回答</h3>
                <StreamingContent
                  content={evaluation.answers}
                  streaming={false}
                  scrollable={false}
                  variant="process"
                />
              </section>
            ) : null}
          </div>
        ) : null}
        {evaluation?.status === "COMPLETED" ? (
          <section className="border-border/60 space-y-2 border-t pt-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold">立为未结议题</h3>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs"
                onClick={() =>
                  setDraft((current) =>
                    current === null ? { finding: "", resolutionTest: "" } : null,
                  )
                }
              >
                {draft === null ? "自己写一条" : "收起"}
              </Button>
            </div>
            {evaluation.candidates.length > 0 ? (
              <div className="space-y-1.5">
                <p className="text-muted-foreground text-xs">
                  评估读完问答后起草了 {evaluation.candidates.length} 条，点一条填进表单。
                  立不立仍然由你决定。
                </p>
                {evaluation.candidates.map((candidate) => (
                  <button
                    key={candidate.finding}
                    type="button"
                    className="border-border/60 hover:bg-muted/60 w-full space-y-1 rounded-md border p-2 text-left"
                    onClick={() =>
                      setDraft({
                        finding: candidate.finding,
                        resolutionTest: candidate.resolution_test,
                      })
                    }
                  >
                    <p className="text-xs leading-relaxed">{candidate.finding}</p>
                    <p className="text-muted-foreground text-xs leading-relaxed">
                      了结条件：{candidate.resolution_test}
                    </p>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground text-xs">
                这次评估没有起草候选。你仍然可以自己写一条。
              </p>
            )}
            {draft !== null ? (
              <div className="space-y-2 pt-1">
                <Field>
                  <FieldLabel htmlFor="finding">发现</FieldLabel>
                  <Textarea
                    id="finding"
                    rows={2}
                    value={draft.finding}
                    placeholder="从上面的问答里挑一条值得追究的，写成一句话"
                    onChange={(event) =>
                      setDraft({ ...draft, finding: event.target.value })
                    }
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="resolution-test">了结条件</FieldLabel>
                  <Textarea
                    id="resolution-test"
                    rows={2}
                    value={draft.resolutionTest}
                    placeholder="什么证据或改动会让这条议题了结"
                    onChange={(event) =>
                      setDraft({ ...draft, resolutionTest: event.target.value })
                    }
                  />
                  <FieldDescription>
                    必填。说不出怎样才算了结的议题，会在此后每一次运行里被回答而永远留在列表上。
                  </FieldDescription>
                </Field>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    raise.isPending ||
                    draft.finding.trim() === "" ||
                    draft.resolutionTest.trim() === ""
                  }
                  onClick={() => raise.mutate()}
                >
                  立项
                </Button>
              </div>
            ) : null}
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
