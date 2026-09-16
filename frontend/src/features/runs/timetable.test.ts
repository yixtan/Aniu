import { describe, expect, it } from "vitest";

import { buildTimetable, marketClock } from "./timetable";
import type { RunSummary } from "@/lib/api-types";

function run(overrides: Partial<RunSummary> & { run_id: number }): RunSummary {
  return {
    task_id: overrides.run_id,
    trigger_source: "scheduled",
    schedule_id: 1,
    status: "COMPLETED",
    current_state: "Summary",
    summary: null,
    summary_render_mode: "markdown",
    started_at: "2026-09-14T01:30:04+00:00",
    completed_at: "2026-09-14T01:33:00+00:00",
    tool_calls_count: 0,
    thinking_count: 0,
    total_tokens: 0,
    cached_tokens: 0,
    trade_count: 0,
    ...overrides,
  };
}

/** 09:30 Beijing, expressed in UTC so the test proves the conversion. */
const NINE_THIRTY = "2026-09-14T01:30:04+00:00";
const NINE_FIFTY = "2026-09-14T01:50:11+00:00";
const NOON = new Date("2026-09-14T04:00:00+00:00");

describe("marketClock", () => {
  it("reads a UTC instant as its Beijing wall clock", () => {
    expect(marketClock(NINE_THIRTY)).toBe("09:30");
  });

  it("does not crash on an unparseable timestamp", () => {
    expect(marketClock("not a date")).toBe("--:--");
  });
});

describe("buildTimetable", () => {
  it("reads the wire's own spelling of scheduled", () => {
    // The API sends TriggerSource.value, which is lowercase. Written as
    // "SCHEDULED" here once, and every scheduled run fell off its planned time
    // and reappeared as an unplanned chip — with the fixture agreeing, so the
    // tests passed. The fixtures above now carry exactly what the API sends.
    const slots = buildTimetable({
      plannedTimes: ["09:30"],
      runs: [
        run({ run_id: 20260914101, trigger_source: "scheduled", started_at: NINE_THIRTY }),
      ],
      now: NOON,
    });

    expect(slots).toEqual([
      expect.objectContaining({ label: "09:30", status: "completed", unplanned: false }),
    ]);
  });

  it("puts a run on the planned time it belongs to", () => {
    const slots = buildTimetable({
      plannedTimes: ["09:30", "09:50"],
      runs: [run({ run_id: 20260914101, started_at: NINE_THIRTY })],
      now: NOON,
    });

    expect(slots.map((slot) => [slot.label, slot.status])).toEqual([
      ["09:30", "completed"],
      ["09:50", "missed"],
    ]);
  });

  it("matches a late start backwards to the time that triggered it", () => {
    // An analysis can wait out a watch, and a queued job takes a moment to be
    // claimed, so a 09:30 run may not start at 09:30:00.
    const slots = buildTimetable({
      plannedTimes: ["09:30", "09:50"],
      runs: [run({ run_id: 20260914101, started_at: "2026-09-14T01:31:47+00:00" })],
      now: NOON,
    });

    expect(slots[0]).toMatchObject({ label: "09:30", status: "completed" });
  });

  it("calls a time that has not come yet pending, not missed", () => {
    const slots = buildTimetable({
      plannedTimes: ["09:30", "14:40"],
      runs: [],
      now: NOON,
    });

    expect(slots.map((slot) => slot.status)).toEqual(["missed", "pending"]);
  });

  it("gives the time that is starting right now a moment before calling it missed", () => {
    // Without the grace the slot being claimed would flash red-grey every time.
    const slots = buildTimetable({
      plannedTimes: ["12:00"],
      runs: [],
      now: new Date("2026-09-14T04:00:30+00:00"),
    });

    expect(slots[0]?.status).toBe("pending");
  });

  it("reports a stopped run the same as a failed one", () => {
    // Both mean the slot produced no report, which is what the page is about.
    const slots = buildTimetable({
      plannedTimes: ["09:30"],
      runs: [run({ run_id: 20260914101, status: "ABORTED", started_at: NINE_THIRTY })],
      now: NOON,
    });

    expect(slots[0]?.status).toBe("failed");
  });

  it("shows a manual run beside the timetable instead of on it", () => {
    const slots = buildTimetable({
      plannedTimes: ["09:30"],
      runs: [
        run({ run_id: 20260914101, started_at: NINE_THIRTY }),
        run({
          run_id: 20260914102,
          trigger_source: "manual",
          schedule_id: null,
          started_at: NINE_FIFTY,
        }),
      ],
      now: NOON,
    });

    expect(slots.map((slot) => [slot.label, slot.unplanned])).toEqual([
      ["09:30", false],
      ["09:50", true],
    ]);
  });

  it("never hides a second run that lands on a taken time", () => {
    const slots = buildTimetable({
      plannedTimes: ["09:30"],
      runs: [
        run({ run_id: 20260914101, started_at: NINE_THIRTY }),
        run({ run_id: 20260914102, started_at: "2026-09-14T01:35:00+00:00" }),
      ],
      now: NOON,
    });

    expect(slots).toHaveLength(2);
    expect(slots[1]).toMatchObject({ label: "09:35", unplanned: true });
  });

  it("draws a day with no plan from its runs alone", () => {
    // How every past day is drawn: the schedule's times describe today's
    // settings, not what that day was actually running.
    const slots = buildTimetable({
      plannedTimes: [],
      runs: [
        run({ run_id: 20260911102, started_at: NINE_FIFTY }),
        run({ run_id: 20260911101, started_at: NINE_THIRTY }),
      ],
      now: NOON,
    });

    expect(slots.map((slot) => slot.label)).toEqual(["09:30", "09:50"]);
    expect(slots.every((slot) => slot.unplanned)).toBe(true);
  });
});
