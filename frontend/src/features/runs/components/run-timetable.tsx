import { cn } from "@/lib/utils";
import type { SlotStatus, TimetableSlot } from "@/features/runs/timetable";

/**
 * One task's day, as the times it was meant to run at.
 *
 * A chip is a planned time whether or not anything happened at it, which is
 * the point: a run that did not happen leaves a hole you can see, and holes
 * are the failure this page exists to show.
 */

const STATUS_LABELS: Record<SlotStatus, string> = {
  pending: "未运行",
  missed: "未运行",
  running: "运行中",
  completed: "已完成",
  failed: "已失败",
};

const STATUS_STYLES: Record<SlotStatus, string> = {
  pending: "border-input text-muted-foreground bg-transparent",
  // Dashed, because a time that has passed with nothing on it is not the same
  // as one that has not come yet — and the watch collides with the analysis
  // twice a day by design, so this state is normal rather than alarming.
  missed: "border-input border-dashed text-muted-foreground/70 bg-transparent",
  // No `dark:` variants anywhere here: the app has a single light palette and
  // no toggle, while Tailwind's `dark:` would follow the operating system —
  // recolouring these chips on a dark-set machine and nothing around them.
  running: "border-sky-500/45 bg-sky-500/[0.10] text-sky-700",
  completed: "border-emerald-500/45 bg-emerald-500/[0.10] text-emerald-700",
  failed: "border-destructive/50 bg-destructive/[0.10] text-destructive",
};

function countBy(slots: TimetableSlot[], status: SlotStatus) {
  return slots.filter((slot) => slot.status === status).length;
}

export function RunTimetable({
  title,
  hint,
  slots,
  selectedRunId,
  onSelectSlot,
}: {
  title: string;
  hint: string;
  slots: TimetableSlot[];
  selectedRunId: number | null;
  onSelectSlot: (slot: TimetableSlot) => void;
}) {
  const completed = countBy(slots, "completed");
  const failed = countBy(slots, "failed");
  const missed = countBy(slots, "missed");

  return (
    <section aria-label={title} className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-foreground text-sm font-semibold tracking-tight">{title}</h2>
        <p className="text-muted-foreground text-xs tabular-nums">
          {slots.length} 个时点 · 已完成 {completed}
          {failed > 0 ? ` · 已失败 ${failed}` : ""}
          {missed > 0 ? ` · 未运行 ${missed}` : ""}
        </p>
        <p className="text-muted-foreground/80 text-xs">{hint}</p>
      </div>

      {slots.length === 0 ? (
        <p className="text-muted-foreground border-border/60 rounded-md border border-dashed px-3 py-4 text-center text-xs">
          这一天没有排定的时点，也没有运行记录
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {slots.map((slot) => {
            const isSelected = slot.run !== null && slot.run.run_id === selectedRunId;
            return (
              <button
                key={slot.id}
                type="button"
                disabled={slot.run === null}
                onClick={() => onSelectSlot(slot)}
                aria-pressed={isSelected}
                aria-label={`${slot.label} ${STATUS_LABELS[slot.status]}${
                  slot.unplanned ? "（计划外）" : ""
                }`}
                title={
                  slot.run === null
                    ? `${slot.label} · ${STATUS_LABELS[slot.status]}`
                    : `${slot.label} · ${STATUS_LABELS[slot.status]} · 运行 ${slot.run.task_id}`
                }
                className={cn(
                  "focus-visible:ring-ring/50 relative rounded-md border px-2.5 py-1 text-xs font-medium tabular-nums outline-none transition-[background-color,border-color,box-shadow] focus-visible:ring-[3px]",
                  STATUS_STYLES[slot.status],
                  slot.run === null ? "cursor-default" : "hover:border-ring/60",
                  isSelected && "ring-ring/35 ring-[3px]",
                )}
              >
                {slot.label}
                {slot.unplanned ? (
                  <span
                    aria-hidden
                    className="bg-foreground/40 absolute end-0.5 top-0.5 size-1 rounded-full"
                  />
                ) : null}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
