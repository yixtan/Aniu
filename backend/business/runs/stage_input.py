"""Summary input: the Run's report, and the budget it has to fit.

The Summary stage only lays the report out as HTML. Its prompt says to keep
every number and conclusion and add none, so the report is all it reads.

It used to be sent the Run's reasoning and tool results as well, trimmed to
whatever fitted. On 2026-09-29 that was 66,000 to 125,000 characters of
reasoning around a report of 2,500 to 3,400: about 97% of every Summary call
was text the prompt tells the model not to use, at 43,000 to 70,000 tokens a
call.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.business.runs.execution import RunReport
from backend.business.shared.serialization import serialize_context
from backend.llm import estimate_tokens


class SummaryInputTooLargeError(ValueError):
    """The report cannot fit the summary context."""


def build_summary_stage_payload(
    report: RunReport,
    *,
    max_characters: int | None = None,
    max_tokens: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, object]:
    """The report, refused rather than clipped when it does not fit.

    Every limit given is honoured at once; see ``_payload_fits_budget``.
    """

    fits = _payload_fits_budget(
        max_characters=max_characters,
        max_tokens=max_tokens,
        max_bytes=max_bytes,
    )
    if fits is None:
        raise SummaryInputTooLargeError("summary input budget is empty")
    payload: dict[str, object] = {"run_report_markdown": report.content}
    if not fits(payload):
        raise SummaryInputTooLargeError("run report exceeds summary budget")
    return payload


def _payload_fits_budget(
    *,
    max_characters: int | None = None,
    max_tokens: int | None = None,
    max_bytes: int | None = None,
) -> Callable[[dict[str, object]], bool] | None:
    """Combine whatever limits the caller gave, and obey the strictest.

    Tokens bound what the model can read; bytes bound what the transport in
    front of it will carry, and the two do not convert. A payload of ASCII
    JSON is four bytes a token, Chinese prose is closer to three characters —
    so the same token budget is 240KB one day and 180KB the next, and a
    gateway that caps message size rejects only the first. Both are measured
    on the same serialized text, so neither can be inferred from the other.
    """

    measures: list[tuple[int, Callable[[str], int]]] = [
        (limit, measure)
        for limit, measure in (
            (max_tokens, estimate_tokens),
            (max_characters, len),
            (max_bytes, lambda text: len(text.encode("utf-8"))),
        )
        if limit is not None
    ]
    if not measures:
        return None
    # A budget of zero on any axis leaves nothing to send.
    if any(limit < 1 for limit, _ in measures):
        return None

    def fits(payload: dict[str, object]) -> bool:
        serialized = serialize_context(payload)
        return all(measure(serialized) <= limit for limit, measure in measures)

    return fits
