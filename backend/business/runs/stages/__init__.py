"""Stage implementations.

Run and Summary are the analysis run's two stages; Watch is an order watch,
which is a run of its own and consists of that one stage. The older "research /
decision / trade / summary" split no longer exists — the first three all happen
inside Run.

Import concrete modules directly; this package stays empty to avoid cycles.
"""

__all__: list[str] = []
