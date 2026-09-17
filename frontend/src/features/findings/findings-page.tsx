import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, ScaleIcon } from "lucide-react";
import { toast } from "sonner";

import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { findingKeys } from "@/features/findings/query-keys";
import { closeOpenFinding, listOpenFindings } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";
import { cn } from "@/lib/utils";

/** How many reach a run at once; mirrors MAX_OPEN_FINDINGS on the backend. */
const MAX_INJECTED = 5;
/** After this many answers that changed nothing, the item asks to be looked at. */
const TALKED_PAST = 3;

export function FindingsPage() {
  const queryClient = useQueryClient();
  const listQuery = useQuery({
    queryKey: findingKeys.list(),
    queryFn: listOpenFindings,
  });
  const close = useMutation({
    mutationFn: closeOpenFinding,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: findingKeys.all });
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
              onClick={() => close.mutate(item.finding_id)}
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
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">已被 {item.dispositions.length} 次运行处置</Badge>
              {item.times_disputed > 0 ? (
                <Badge
                  variant="outline"
                  className={cn(
                    item.times_disputed >= TALKED_PAST && "border-amber-500 text-amber-700",
                  )}
                >
                  其中 {item.times_disputed} 次未做调整
                </Badge>
              ) : null}
            </div>
            {item.dispositions.length > 0 ? (
              <ul className="text-muted-foreground space-y-1">
                {item.dispositions.map((disposition) => (
                  <li key={`${disposition.run_id}-${disposition.at}`}>
                    运行 {disposition.run_id} · {verdictLabel(disposition.verdict)}
                    {disposition.note ? ` · ${disposition.note}` : ""}
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
            <p key={item.finding_id} className="text-muted-foreground text-xs line-through">
              {item.finding}
            </p>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function verdictLabel(verdict: string) {
  if (verdict === "ADJUSTED") return "已按此调整";
  if (verdict === "DISAGREED") return "不同意";
  return "尚无法判断";
}
