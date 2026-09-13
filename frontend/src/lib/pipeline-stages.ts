/** Frontend mirror of the backend stage metadata.
 *
 * Run and Summary are the analysis run's two stages. Watch is an order watch,
 * a run of its own with that one stage — it appears in traces and run lists,
 * so it needs a label, but it is not a step an analysis run passes through.
 */

type PipelineStageId = "Run" | "Summary" | "Watch";

type PipelineStageDef = {
  stageId: PipelineStageId;
  shortLabel: string;
};

const STAGE_PIPELINE: readonly PipelineStageDef[] = [
  { stageId: "Run", shortLabel: "操盘" },
  { stageId: "Summary", shortLabel: "总结" },
  { stageId: "Watch", shortLabel: "盯盘" },
] as const;

function stageById(stageId: string | null | undefined): PipelineStageDef | null {
  if (!stageId) return null;
  for (const stage of STAGE_PIPELINE) {
    if (stageId === stage.stageId || stageId.startsWith(stage.stageId)) return stage;
  }
  return null;
}

export function formatStageState(state: string | null | undefined): string {
  if (!state) return "--";
  if (state === "Completed" || state === "COMPLETED") return "已完成";
  if (state === "Failed" || state === "FAILED") return "失败";
  return stageById(state)?.shortLabel ?? state;
}
