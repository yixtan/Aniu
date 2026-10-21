import type { ReturnCurveData } from "./return-curve";

function timestamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

export function EquityRecordingNotice({ status }: { status: ReturnCurveData["recording_status"] }) {
  if (!status) return null;
  if (!status.error_message) {
    return (
      <p className="text-muted-foreground mb-3 text-xs">
        净值最近保存成功：{timestamp(status.last_success_at ?? status.attempted_at)}
      </p>
    );
  }
  return (
    <div
      role="alert"
      aria-label="净值保存异常"
      className="mb-3 space-y-1 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-sm"
    >
      <p className="font-medium">{status.trade_date} 的最新净值未保存</p>
      <p className="text-muted-foreground text-xs">
        该次账户刷新已完成，但净值未写入历史；下方保留此前已保存的记录。
      </p>
      <p className="text-xs">{status.error_message}</p>
      <p className="text-muted-foreground text-xs">排除问题后，点击页面上方“刷新数据”重新保存。</p>
      <p className="text-muted-foreground text-xs">
        失败时间：{timestamp(status.attempted_at)}；
        {status.last_success_at
          ? `上次保存成功：${timestamp(status.last_success_at)}`
          : "尚无保存成功的状态记录"}
      </p>
    </div>
  );
}
