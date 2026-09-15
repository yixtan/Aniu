"""Refusing a call that has already failed this turn with the same arguments."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

MAX_IDENTICAL_FAILURES = 2
"""How many times one identical call may fail before it stops being allowed.

Two, so a genuinely transient failure still gets its retry. The third attempt
is refused, because by then the evidence says the arguments are the problem
and nothing about them has changed.
"""


def _key(tool_name: str, arguments: object) -> tuple[str, str]:
    """Identify a call by what it asks, not by when it was asked.

    Sorted keys so argument order cannot make two identical calls look
    different; a value that will not serialize falls back to its repr, which
    is stable enough to compare against itself.
    """

    try:
        shape = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        shape = repr(arguments)
    return tool_name, shape


@dataclass(slots=True)
class RepeatedFailureLedger:
    """What has already failed in this turn, so it is not asked again.

    On 2026-09-15 a watch met a cancel the exchange would not accept and,
    given no reason, sent it about twenty-five times in fifty seconds. That
    tripped the provider's rate limiter, which then failed the run's account
    reads as well, and the watch died on its deadline. One rejection became an
    outage because nothing counted the repeats.

    Scoped to one prompt run, which is one stage's whole tool loop — the same
    scope the failure had.
    """

    _failures: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)

    def record_failure(self, tool_name: str, arguments: object, error: str) -> None:
        key = _key(tool_name, arguments)
        count, _ = self._failures.get(key, (0, ""))
        self._failures[key] = (count + 1, error)

    def refusal_for(self, tool_name: str, arguments: object) -> str | None:
        """The reason to refuse this call, or None to let it through.

        The previous error is quoted back rather than summarised: the model is
        about to decide what to do instead, and it cannot do that from the bare
        fact that something failed.
        """

        count, error = self._failures.get(_key(tool_name, arguments), (0, ""))
        if count < MAX_IDENTICAL_FAILURES:
            return None
        return (
            f"{tool_name} 已经用完全相同的参数失败 {count} 次，"
            f"最后一次的错误是：{error}。"
            "参数没有变化，再试一次不会有不同结果，本次调用已被阻止。"
            "请改用别的做法，或在记录里写明这件事没能完成、以及原因。"
        )


__all__ = ["MAX_IDENTICAL_FAILURES", "RepeatedFailureLedger"]
