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

type Closing = { findingId: number; note: string };

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
            在某次运行的「独立评估」里,把值得追究的问题立为议题。立项后每次操盘都要当面表态,
            直到你关闭它。
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-muted-foreground text-xs">
        每条未结议题都会随运行上下文送到操盘阶段,要求逐条表态,空了则整段不发。
        智能体只能表态,关闭权在你 —— 上限 {MAX_INJECTED} 条,超出的不会送出去。
      </p>
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
                    : { findingId: item.finding_id, note: "" },
                )
              }
            >
              <CheckIcon aria-hidden className="size-3.5" />
              关闭
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
                <Badge variant="outline">已被 {item.dispositions.length} 次运行处置</Badge>
                {/* The newest verdict, so "this looks done" is one glance rather
                    than four paragraphs of disposition notes. */}
                <Badge variant="outline">
                  最近：{verdictLabel(latestVerdict(item.dispositions) ?? "")}
                </Badge>
                {item.settlement_proposed ? (
                  <Badge variant="outline" className="border-emerald-500 text-emerald-700">
                    运行认为可了结 —— 等你确认
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
                    其中 {item.times_disputed} 次未做调整
                  </Badge>
                ) : null}
              </div>
            ) : null}
            {closing?.findingId === item.finding_id ? (
              <div className="border-border/60 space-y-2 rounded-md border p-2">
                <label
                  className="text-foreground block text-xs font-medium"
                  htmlFor={`closing-note-${item.finding_id}`}
                >
                  了结说明（必填）
                </label>
                <Input
                  id={`closing-note-${item.finding_id}`}
                  value={closing.note}
                  placeholder="是了结条件被满足了，还是这条议题作废了？"
                  onChange={(event) =>
                    setClosing({ ...closing, note: event.target.value })
                  }
                />
                <p className="text-muted-foreground text-xs">
                  没有这一句，「按证据了结」和「问错了，放弃」在记录里是同一行。
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    disabled={close.isPending || closing.note.trim() === ""}
                    onClick={() =>
                      close.mutate({
                        findingId: item.finding_id,
                        note: closing.note,
                      })
                    }
                  >
                    确认关闭
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setClosing(null)}>
                    取消
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
                      运行 {disposition.run_id} · {verdictLabel(disposition.verdict)}
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
                  <span className="text-foreground font-medium">了结说明：</span>
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
  if (verdict === "ADJUSTED") return "已按此调整";
  if (verdict === "SETTLED") return "认为可了结";
  if (verdict === "DISAGREED") return "不同意";
  return "尚无法判断";
}

function latestVerdict(dispositions: { verdict: string }[]) {
  return dispositions.at(-1)?.verdict ?? null;
}
