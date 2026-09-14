import type { RunSummary } from "@/lib/api-types";

/**
 * A day's runs laid out against the times they were supposed to happen at.
 *
 * Only today has planned times: the schedule endpoint derives them from the
 * *current* settings, so applying them to an older day would draw slots that
 * never existed on it. A past day is therefore drawn from its runs alone —
 * every chip is something that really happened.
 */

/** Market times are Beijing times, and so are the schedule's. Read explicitly
 * rather than through the browser's zone so the page is right on a laptop that
 * travelled, and so tests do not depend on the runner's TZ. */
const MARKET_TIMEZONE = "Asia/Shanghai";

const MARKET_CLOCK = new Intl.DateTimeFormat("en-GB", {
  timeZone: MARKET_TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/**
 * How long a planned time may stand empty before it is called missed.
 *
 * A run appears the moment it is created, but the analysis can wait up to 30
 * seconds for a watch to finish and a queued job takes a moment to claim. With
 * no grace the slot that is starting right now would flash as missed.
 */
const SLOT_GRACE_MINUTES = 2;

export type SlotStatus = "pending" | "missed" | "running" | "completed" | "failed";

export type TimetableSlot = {
  /** Stable identity for React keys and for which chip is selected. */
  id: string;
  /** "09:30" — the planned time, or the real start time when unplanned. */
  label: string;
  run: RunSummary | null;
  status: SlotStatus;
  /**
   * A run the timetable did not plan for: a manual run, or a scheduled one
   * that does not line up with any planned time. Shown anyway — a page about
   * what ran today must never be the reason a run is invisible.
   */
  unplanned: boolean;
};

/**
 * Which day's page a moment belongs to, as "YYYY-MM-DD".
 *
 * The UTC date, because that is what the run id encodes and what the day
 * filter compares against — not the Beijing date, which differs from it
 * between midnight and 08:00 and would name a day holding no runs.
 */
export function runDayOf(moment: Date): string {
  return moment.toISOString().slice(0, 10);
}

/** The ninth digit of a task id says who ran; 3 is the order watch. */
export function isOrderWatchTask(taskId: number): boolean {
  return String(taskId).charAt(8) === "3";
}

export function marketClock(value: string | Date): string {
  const moment = typeof value === "string" ? new Date(value) : value;
  return Number.isNaN(moment.getTime()) ? "--:--" : MARKET_CLOCK.format(moment);
}

function minutesOf(clock: string): number {
  const [hours, minutes] = clock.split(":");
  return Number(hours) * 60 + Number(minutes);
}

function statusOfRun(run: RunSummary): SlotStatus {
  if (run.status === "RUNNING") return "running";
  if (run.status === "COMPLETED") return "completed";
  // FAILED and ABORTED both mean the slot produced no report.
  return "failed";
}

/**
 * Assign each run to the latest planned time at or before it started.
 *
 * A run never starts before the time that triggered it, and it can start well
 * after — queued behind another run, or waiting out a watch. So the planned
 * time is found by looking backwards, not by matching the clock exactly.
 */
function planRuns(
  planned: readonly string[],
  runs: readonly RunSummary[],
): { taken: Map<string, RunSummary>; unplanned: RunSummary[] } {
  const minutes = planned.map(minutesOf);
  const taken = new Map<string, RunSummary>();
  const unplanned: RunSummary[] = [];

  for (const run of [...runs].sort((left, right) => left.run_id - right.run_id)) {
    if (run.trigger_source !== "SCHEDULED") {
      unplanned.push(run);
      continue;
    }
    const startedAt = minutesOf(marketClock(run.started_at));
    let slot: string | null = null;
    for (let index = 0; index < planned.length; index += 1) {
      const time = planned[index];
      if (time === undefined || (minutes[index] ?? 0) > startedAt) break;
      slot = time;
    }
    // An already-taken slot keeps its first run; a second one is shown on its
    // own rather than replacing the first or disappearing.
    if (slot === null || taken.has(slot)) unplanned.push(run);
    else taken.set(slot, run);
  }
  return { taken, unplanned };
}

export function buildTimetable(options: {
  /** Planned times as "HH:MM". Empty for a past day or a disabled schedule. */
  plannedTimes: readonly string[];
  /** The day's runs for one task only — analysis or watch, never both. */
  runs: readonly RunSummary[];
  now: Date;
}): TimetableSlot[] {
  const { plannedTimes, runs, now } = options;
  const { taken, unplanned } = planRuns(plannedTimes, runs);
  const nowMinutes = minutesOf(marketClock(now));

  const plannedSlots: TimetableSlot[] = plannedTimes.map((time) => {
    const run = taken.get(time) ?? null;
    return {
      id: time,
      label: time,
      run,
      status:
        run !== null
          ? statusOfRun(run)
          : nowMinutes >= minutesOf(time) + SLOT_GRACE_MINUTES
            ? "missed"
            : "pending",
      unplanned: false,
    };
  });

  const extraSlots: TimetableSlot[] = unplanned.map((run) => ({
    id: `run-${run.run_id}`,
    label: marketClock(run.started_at),
    run,
    status: statusOfRun(run),
    unplanned: true,
  }));

  return [...plannedSlots, ...extraSlots].sort(
    (left, right) => minutesOf(left.label) - minutesOf(right.label),
  );
}
