import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, ScaleIcon } from "lucide-react";
import { toast } from "sonner";

import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { findingKeys } from "@/features/findings/query-keys";
import { closeOpenFinding, listOpenFindings } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How many reach a run at once; mirrors MAX_OPEN_FINDINGS on the backend. */
const MAX_INJECTED = 5;
/** After this many answers that changed nothing, the item asks to be looked at. */
const TALKED_PAST = 3;

type Outcome = "MET" | "WITHDRAWN";

/** Named, not asked. The old form put an open question above a blank box. */
const OUTCOMES: { value: Outcome; label: string; hint: string }[] = [
  {
    value: "MET",
    label: "做到了，可以结了",
    hint: "上面那条了结条件，智能体已经做到",
  },
  {
    value: "WITHDRAWN",
    label: "这条不问了",
    hint: "问题本身不成立，或者已经不重要了",
  },
];
type Closing = { findingId: number; outcome: Outcome | null; note: string };

export function FindingsPage() {
  const queryClient = useQueryClient();
  const listQuery = useQuery({
    queryKey: findingKeys.list(),
    queryFn: listOpenFindings,
  });
  // Null until the operator picks one to close: closing now asks for a
  // sentence, and a permanently open box on every card would read as noise.
  const [closing, setClosing] = useState<Closing | null>(null);
  const close = useMutation({
    mutationFn: closeOpenFinding,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: findingKeys.all });
      setClosing(null);
      toast.success("议题已关闭");
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  if (listQuery.isLoading) {
    return <QueryLoadingState label="正在加载未结议题…" />;
  }
  if (listQuery.isError) {
    return (
      <QueryErrorState error={listQuery.error} onRetry={() => void listQuery.refetch()} />
    );
  }

  const findings = listQuery.data ?? [];
  const open = findings.filter((item) => item.status === "OPEN");
  const closed = findings.filter((item) => item.status !== "OPEN");

  if (findings.length === 0) {
    return (
      <Empty className="min-h-[240px] justify-center">
        <EmptyHeader>
          <EmptyTitle>还没有未结议题</EmptyTitle>
          <EmptyDescription>
            去某次运行的「独立评估」里挑一条值得追究的,立成议题。
            立了之后,每次操盘都要当面回答它,直到你关掉为止。
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="text-muted-foreground space-y-1 text-xs">
        <p>
          这里挂着的每条议题,每次操盘都会摆到智能体面前,要求它当面回答 ——
          它可以说「改了」「不同意」「还看不出来」,
          <span className="text-foreground font-medium">但它划不掉任何一条</span>。
        </p>
        <p>
          什么时候算完,由你说了算:对着每条自己的「了结条件」看一眼,做到了就关掉它。
          最多同时挂 {MAX_INJECTED} 条,多出来的不会送给它。
        </p>
      </div>
      {open.map((item) => (
        <Card key={item.finding_id} className="gap-2 py-4">
          <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-0">
            <CardTitle className="flex items-start gap-2 text-sm leading-relaxed font-medium">
              <ScaleIcon aria-hidden className="text-muted-foreground mt-0.5 size-4 shrink-0" />
              {item.finding}
            </CardTitle>
            <Button
              size="sm"
              variant="outline"
              disabled={close.isPending}
              onClick={() =>
                setClosing((current) =>
                  current?.findingId === item.finding_id
                    ? null
                    : { findingId: item.finding_id, outcome: null, note: "" },
                )
              }
            >
              <CheckIcon aria-hidden className="size-3.5" />
              结掉这条
            </Button>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              <span className="text-foreground font-medium">了结条件：</span>
              {item.resolution_test}
            </p>
            {/* Nothing at all until a run has answered: "已被 0 次运行处置" on a
                finding raised a minute ago is a row of chrome saying nothing. */}
            {item.dispositions.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">智能体回答过 {item.dispositions.length} 次</Badge>
                {/* The newest verdict, so "this looks done" is one glance rather
                    than four paragraphs of disposition notes. */}
                <Badge variant="outline">
                  最近：{verdictLabel(latestVerdict(item.dispositions) ?? "")}
                </Badge>
                {item.settlement_proposed ? (
                  <Badge variant="outline" className="border-emerald-500 text-emerald-700">
                    它说已经做到了 —— 等你确认
                  </Badge>
                ) : null}
                {item.times_disputed > 0 ? (
                  <Badge
                    variant="outline"
                    className={cn(
                      item.times_disputed >= TALKED_PAST &&
                        "border-amber-500 text-amber-700",
                    )}
                  >
                    其中 {item.times_disputed} 次没有改动
                  </Badge>
                ) : null}
              </div>
            ) : null}
            {closing?.findingId === item.finding_id ? (
              <div className="border-border/60 space-y-3 rounded-md border p-3">
                {/* The thing being judged, at the moment of judging. It is also
                    at the top of the card, but not on screen while you type. */}
                <div className="space-y-1">
                  <p className="text-foreground text-xs font-medium">
                    先看一眼当初说好的了结条件
                  </p>
                  <p className="text-muted-foreground bg-muted/50 rounded px-2 py-1.5 text-xs leading-relaxed">
                    {item.resolution_test}
                  </p>
                </div>
                <div className="space-y-1.5">
                  <p className="text-foreground text-xs font-medium">这条怎么收尾？</p>
                  {OUTCOMES.map((choice) => (
                    <button
                      key={choice.value}
                      type="button"
                      aria-pressed={closing.outcome === choice.value}
                      className={cn(
                        "border-border/60 hover:bg-muted/60 w-full rounded-md border px-2 py-1.5 text-left",
                        closing.outcome === choice.value && "border-primary bg-muted",
                      )}
                      onClick={() => setClosing({ ...closing, outcome: choice.value })}
                    >
                      <span className="text-foreground block text-xs font-medium">
                        {choice.label}
                      </span>
                      <span className="text-muted-foreground block text-xs">
                        {choice.hint}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="space-y-1">
                  <label
                    className="text-foreground block text-xs font-medium"
                    htmlFor={`closing-note-${item.finding_id}`}
                  >
                    说点什么（必填）
                  </label>
                  <Input
                    id={`closing-note-${item.finding_id}`}
                    value={closing.note}
                    placeholder="一句话就行，比如「阈值已经写进记忆 144」"
                    onChange={(event) =>
                      setClosing({ ...closing, note: event.target.value })
                    }
                  />
                  <p className="text-muted-foreground text-xs">
                    以后回头看，就靠这一句想起当时为什么关掉它。
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    disabled={
                      close.isPending ||
                      closing.outcome === null ||
                      closing.note.trim() === ""
                    }
                    onClick={() =>
                      closing.outcome === null
                        ? undefined
                        : close.mutate({
                            findingId: item.finding_id,
                            outcome: closing.outcome,
                            note: closing.note,
                          })
                    }
                  >
                    关掉它
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setClosing(null)}>
                    再想想
                  </Button>
                </div>
              </div>
            ) : null}
            {item.dispositions.length > 0 ? (
              /* Which run said what goes on its own line, and the reasoning
                 below it. These notes run to three hundred characters with no
                 line breaks of their own, so run id, verdict and argument on
                 one line is a wall nobody reads. */
              <ul className="space-y-2">
                {item.dispositions.map((disposition) => (
                  <li
                    key={`${disposition.run_id}-${disposition.at}`}
                    className="border-border/60 space-y-0.5 border-s ps-2"
                  >
                    <p className="text-foreground text-xs font-medium tabular-nums">
                      第 {disposition.run_id} 次操盘 · {verdictLabel(disposition.verdict)}
                    </p>
                    {disposition.note ? (
                      <p className="text-muted-foreground text-xs leading-relaxed">
                        {disposition.note}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </CardContent>
        </Card>
      ))}
      {closed.length > 0 ? (
        <section className="space-y-2">
          <h2 className="text-muted-foreground text-xs font-medium">已关闭</h2>
          {closed.map((item) => (
            <div key={item.finding_id} className="space-y-0.5">
              <p className="text-muted-foreground text-xs line-through">{item.finding}</p>
              {item.closing_note ? (
                <p className="text-muted-foreground text-xs">
                  <span className="text-foreground font-medium">
                    {outcomeLabel(item.closing_outcome)}：
                  </span>
                  {item.closing_note}
                </p>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function verdictLabel(verdict: string) {
  if (verdict === "ADJUSTED") return "按这条改了";
  if (verdict === "SETTLED") return "说已经做到了";
  if (verdict === "DISAGREED") return "不同意";
  return "还看不出来";
}

function outcomeLabel(outcome: string) {
  if (outcome === "MET") return "做到了";
  if (outcome === "WITHDRAWN") return "不问了";
  // Closed before the two endings were told apart.
  return "关闭原因";
}

function latestVerdict(dispositions: { verdict: string }[]) {
  return dispositions.at(-1)?.verdict ?? null;
}
