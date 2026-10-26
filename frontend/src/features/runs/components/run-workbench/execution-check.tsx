import { useMutation, useQuery } from "@tanstack/react-query";
import { getExecutionCheck, refreshAccountCache } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";
import { Button } from "@/components/ui/button";

const orderStatuses: Record<string, string> = {
  PENDING: "待成交",
  PARTIAL: "部分成交",
  FILLED: "全部成交",
  CANCELLED: "已撤单",
  PARTIAL_CANCELLED: "部分成交后撤单",
  REJECTED: "已拒绝或作废",
  PENDING_CANCEL: "撤单处理中",
  PARTIAL_PENDING_CANCEL: "部分成交，撤单处理中",
};
const outcomes: Record<string, string> = {
  unknown: "结果待确认",
  accepted: "已受理，未确认成交",
  filled: "已全部成交",
  partial: "已部分成交",
  closed: "已终结，未成交",
  cancelled: "已确认撤单",
};

export function ExecutionCheckPanel({ runId, status }: { runId: number; status: string }) {
  const query = useQuery({
    queryKey: ["execution-check", runId, status],
    queryFn: () => getExecutionCheck(runId),
    refetchInterval: 30_000,
  });
  const refresh = useMutation({
    mutationFn: refreshAccountCache,
    onSuccess: async () => {
      await query.refetch();
    },
  });
  const data = query.data;
  return (
    <section className="bg-muted/20 mb-4 rounded-lg border p-4" aria-label="交易结果核对">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">交易结果核对</h3>
        <Button
          variant="outline"
          size="sm"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? "正在核对…" : "刷新账户并核对"}
        </Button>
      </div>
      {query.isPending && <p className="mt-2 text-sm">正在读取交易记录…</p>}
      {(query.error || refresh.error) && (
        <p role="alert" className="text-destructive mt-2 text-sm">
          {getErrorMessage(query.error || refresh.error)}
        </p>
      )}
      {refresh.data?.status === "stale" && (
        <p role="alert" className="mt-2 text-sm text-amber-700">
          账户刷新未成功，仍显示上次观测结果。
        </p>
      )}
      {data && (
        <div className="mt-2 space-y-2 text-sm">
          {data.interrupted && (
            <p className="font-medium text-amber-700">
              运行中断不代表交易未发生。请先核对下面的委托结果。
            </p>
          )}
          {data.unfinished_steps.length > 0 && <p>未完成：{data.unfinished_steps.join("、")}</p>}
          <p className="text-muted-foreground">
            账户观测时间：
            {data.account_observed_at
              ? new Date(data.account_observed_at).toLocaleString("zh-CN")
              : "尚无账户数据"}
            {data.evidence_stale ? " · 数据待刷新" : ""}
          </p>
          {data.items.map((item) => (
            <div className="rounded border p-3" key={item.tool_call_id}>
              <p className="font-medium">
                {item.instruction} · {outcomes[item.outcome] ?? "结果待确认"}
              </p>
              <p>
                {item.order_id ? `委托编号 ${item.order_id}` : "没有可核对的委托编号"}
                {item.order_status ? ` · ${orderStatuses[item.order_status] ?? "状态待核对"}` : ""}
                {item.filled_quantity !== null ? ` · 已成交 ${item.filled_quantity} 股` : ""}
              </p>
              <p className="text-muted-foreground">{item.explanation}</p>
            </div>
          ))}
          {data.items.length === 0 && <p>没有可展示的交易调用记录。</p>}
          <p className="text-muted-foreground">{data.notice}</p>
        </div>
      )}
    </section>
  );
}
