import { cn } from "@/lib/utils";

/**
 * The number a record is called by everywhere except the screen.
 *
 * Reports, dream logs, lineage badges and every conversation about this
 * account name runs, findings, memories and evaluations by id. The pages
 * showed the prose and left the number in a `title` attribute, which a phone
 * never reveals — so anything said about 「议题 #11」 or 「运行 20260922106」
 * could not be matched to what was on screen.
 *
 * Not a button and not inside one: an id is there to be read and copied, and
 * a tap on it should do nothing.
 */
export function RecordId({
  id,
  label,
  className,
}: {
  id: number | string;
  label: string;
  className?: string;
}) {
  return (
    <span
      title={`${label} #${id}`}
      className={cn(
        "bg-primary/10 text-primary shrink-0 rounded px-1.5 font-semibold tabular-nums",
        className,
      )}
    >
      #{id}
    </span>
  );
}
