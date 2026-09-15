"""Process-local abort registry for the run executors."""

from __future__ import annotations

from backend.business.runs.abort import RunAbortSignal


class ActiveRunAbortRegistry:
    """Bridge an API abort request to the executor running it in this process.

    Keyed by run id rather than holding one signal, because more than one run
    can be in flight: an analysis renders its HTML summary on its own lane
    while an order watch runs on the exclusive one. With a single slot the
    second run to start silently took the first one's place, and an abort
    aimed at the first — by the user, or by the worker losing its lease —
    would quietly do nothing.
    """

    def __init__(self) -> None:
        self._signals: dict[int, RunAbortSignal] = {}

    @property
    def active_run_ids(self) -> frozenset[int]:
        return frozenset(self._signals)

    def signal_for(self, run_id: int) -> RunAbortSignal | None:
        return self._signals.get(run_id)

    def activate(self, run_id: int) -> RunAbortSignal:
        signal = RunAbortSignal(run_id)
        self._signals[run_id] = signal
        return signal

    def abort(self, run_id: int, reason: str) -> bool:
        signal = self._signals.get(run_id)
        if signal is None:
            return False
        signal.abort(reason)
        return True

    def clear(self, signal: RunAbortSignal) -> None:
        # Compared by identity: a re-activated run has a new signal, and the
        # late teardown of the old one must not unregister the live one.
        if self._signals.get(signal.run_id) is signal:
            del self._signals[signal.run_id]
