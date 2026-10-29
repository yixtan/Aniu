import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { RunDetail } from "@/lib/api-types";
import { TradingFactsNotice } from "./trading-facts-notice";

it("shows system evidence independently of model claims", () => {
  const run = {
    status: "COMPLETED",
    trace: {
      stages: [
        {
          key: "run",
          steps: [
            { step_id: "model", content: "已通过新审核，潜在敞口11.87%" },
            {
              step_id: "system_trading_facts",
              content: "## 审核状态与潜在敞口（系统生成）\n\n无下单前审核记录。潜在敞口10.87%。",
            },
          ],
        },
      ],
    },
  } as unknown as RunDetail;
  render(<TradingFactsNotice run={run} />);
  expect(screen.getByText(/无下单前审核记录/)).toBeVisible();
  expect(screen.queryByText(/11.87%/)).not.toBeInTheDocument();
});

it("does not infer approval from a completed historical report", () => {
  const run = {
    status: "COMPLETED",
    trace: { stages: [{ key: "run", steps: [] }] },
  } as unknown as RunDetail;
  render(<TradingFactsNotice run={run} />);
  expect(screen.getByText(/本次没有系统交易事实记录/)).toBeVisible();
});
