import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { RunDetail } from "@/lib/api-types";
import { HandoffNotice } from "./handoff-notice";

it("does not confuse a completed report with a completed handoff", () => {
  const run = {
    status: "COMPLETED",
    trace: {
      stages: [
        {
          key: "run",
          steps: [
            {
              step_id: "order_handoff_check",
              status: "failed",
              summary: "交接未完成",
              content: "合成委托缺少本轮计划",
            },
          ],
        },
      ],
    },
  } as unknown as RunDetail;
  render(<HandoffNotice run={run} />);
  expect(screen.getByText("交接未完成")).toBeInTheDocument();
  expect(screen.getByText("合成委托缺少本轮计划")).toBeInTheDocument();
});
it("does not claim that historical runs passed a new check", () => {
  const run = {
    status: "FAILED",
    trace: { stages: [{ key: "run", steps: [] }] },
  } as unknown as RunDetail;
  render(<HandoffNotice run={run} />);
  expect(screen.getByText(/本次没有系统交接检查记录/)).toBeInTheDocument();
});
