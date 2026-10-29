import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { RunDetail } from "@/lib/api-types";
import { ValuationCancellationsNotice } from "./valuation-cancellations-notice";

it("shows a system request still awaiting confirmation even when model claims cancellation", () => {
  const run = {
    trace: { stages: [{ key: "watch", steps: [
      { step_id: "model", content: "全部已经撤销" },
      { step_id: "system_valuation_cancellations", content: "## 挂单估值自动处置（系统生成）\n\n撤单请求已受理，尚未确认撤销。已成交部分保留。" },
    ] }] },
  } as unknown as RunDetail;
  render(<ValuationCancellationsNotice run={run} />);
  expect(screen.getByText(/尚未确认撤销/)).toBeVisible();
  expect(screen.queryByText("全部已经撤销")).not.toBeInTheDocument();
});

it("does not invent automatic cancellation evidence for historical runs", () => {
  const run = { trace: { stages: [{ steps: [] }] } } as unknown as RunDetail;
  const { container } = render(<ValuationCancellationsNotice run={run} />);
  expect(container).toBeEmptyDOMElement();
});
