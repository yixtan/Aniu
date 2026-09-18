import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EvaluationCard } from "./evaluation-card";

const api = vi.hoisted(() => ({
  getRunEvaluation: vi.fn(),
  requestRunEvaluation: vi.fn(),
  raiseOpenFinding: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const RUN_ID = 20260917101;

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EvaluationCard runId={RUN_ID} />
    </QueryClientProvider>,
  );
}

function evaluation(overrides: Record<string, unknown> = {}) {
  return {
    evaluation_id: 1,
    run_id: RUN_ID,
    status: "COMPLETED",
    questions: "一、你连续三天零成交，证伪条件是什么？",
    answers: "这个数我手上没有，需要调用组合查询工具。",
    candidates: [],
    total_tokens: 26_000,
    cached_tokens: 9_000,
    failure_reason: null,
    created_at: "2026-09-17T08:00:00+00:00",
    started_at: "2026-09-17T08:00:01+00:00",
    completed_at: "2026-09-17T08:01:30+00:00",
    ...overrides,
  };
}

describe("EvaluationCard", () => {
  it("offers to start one when the run has never been reviewed", async () => {
    api.getRunEvaluation.mockResolvedValue(null);

    renderCard();

    expect(await screen.findByRole("button", { name: "开始评估" })).toBeEnabled();
    expect(screen.queryByText("提问")).not.toBeInTheDocument();
  });

  it("shows the questions and the answers, and what they cost", async () => {
    api.getRunEvaluation.mockResolvedValue(evaluation());

    renderCard();

    expect(await screen.findByText(/证伪条件是什么/)).toBeInTheDocument();
    expect(screen.getByText(/需要调用组合查询工具/)).toBeInTheDocument();
    // The cached share reads as part of the total, never beside it.
    expect(screen.getByText(/26,000（缓存命中 9,000）/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新评估" })).toBeEnabled();
  });

  it("renders the answer as Markdown, not as literal hashes", async () => {
    // Both halves answer in Markdown. Rendered as plain text the card filled
    // up with literal ## and **, which is how it shipped.
    api.getRunEvaluation.mockResolvedValue(
      evaluation({
        questions: "## 一、成交率断崖\n\n**连续三天零成交**，请给出证伪条件。",
        answers: "这个数我手上没有。",
      }),
    );

    renderCard();

    const heading = await screen.findByRole("heading", { name: "一、成交率断崖" });
    expect(heading).toBeInTheDocument();
    expect(screen.getByText("连续三天零成交").tagName).toBe("STRONG");
    expect(screen.queryByText(/## 一、成交率断崖/)).not.toBeInTheDocument();
  });

  it("locks the button while one is being written", async () => {
    // A review takes a minute or two; a second press would spend it twice.
    api.getRunEvaluation.mockResolvedValue(evaluation({ status: "RUNNING" }));

    renderCard();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "评估中…" })).toBeDisabled(),
    );
  });

  it("says why a review failed rather than looking like it never ran", async () => {
    api.getRunEvaluation.mockResolvedValue(
      evaluation({
        status: "FAILED",
        questions: null,
        answers: null,
        failure_reason: "provider refused the request",
      }),
    );

    renderCard();

    expect(await screen.findByText("provider refused the request")).toBeInTheDocument();
  });

  it("offers the drafts the review wrote, so nobody types them up", async () => {
    api.getRunEvaluation.mockResolvedValue(
      evaluation({
        candidates: [
          {
            finding: "五笔买入单全属 AI 硬件链，会在同一天一起成交。",
            resolution_test: "把敞口拆到与 AI 硬件链不相关的主线上。",
          },
        ],
      }),
    );

    renderCard();

    const draft = await screen.findByRole("button", {
      name: /五笔买入单全属 AI 硬件链/,
    });
    // Nothing to fill in until one is picked: a long answer followed by two
    // empty boxes is what this replaces.
    expect(screen.queryByLabelText("发现")).not.toBeInTheDocument();

    await userEvent.click(draft);

    expect(await screen.findByLabelText("发现")).toHaveValue(
      "五笔买入单全属 AI 硬件链，会在同一天一起成交。",
    );
    expect(screen.getByLabelText("了结条件")).toHaveValue(
      "把敞口拆到与 AI 硬件链不相关的主线上。",
    );
    expect(screen.getByRole("button", { name: "立项" })).toBeEnabled();
  });

  it("raises what the person confirmed, not what was drafted", async () => {
    // Drafting is not raising. The text is editable between the two, and the
    // edited text is what gets filed.
    api.getRunEvaluation.mockResolvedValue(
      evaluation({
        candidates: [{ finding: "起草的说法。", resolution_test: "起草的了结条件。" }],
      }),
    );
    api.raiseOpenFinding.mockResolvedValue({});

    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /起草的说法/ }));

    const finding = screen.getByLabelText("发现");
    await userEvent.clear(finding);
    await userEvent.type(finding, "我改过的说法。");
    await userEvent.click(screen.getByRole("button", { name: "立项" }));

    await waitFor(() =>
      expect(api.raiseOpenFinding).toHaveBeenCalledWith({
        finding: "我改过的说法。",
        resolution_test: "起草的了结条件。",
        evaluation_id: 1,
      }),
    );
  });

  it("still lets a person write their own when nothing was drafted", async () => {
    // The objection that changed this account's strategy was not among the
    // reviewer's questions; a person read them and wrote a sixth.
    api.getRunEvaluation.mockResolvedValue(evaluation());

    renderCard();

    expect(await screen.findByText(/这次评估没有起草候选/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "自己写一条" }));

    expect(screen.getByLabelText("发现")).toHaveValue("");
    expect(screen.getByRole("button", { name: "立项" })).toBeDisabled();
  });

  it("queues a review when the button is pressed", async () => {
    api.getRunEvaluation.mockResolvedValue(null);
    api.requestRunEvaluation.mockResolvedValue(
      evaluation({ status: "PENDING", questions: null, answers: null }),
    );

    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: "开始评估" }));

    expect(api.requestRunEvaluation).toHaveBeenCalledWith(RUN_ID);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "评估中…" })).toBeDisabled(),
    );
  });
});
