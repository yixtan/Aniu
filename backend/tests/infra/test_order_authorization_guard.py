"""The watch may act where a plan says so, and nowhere else."""

from __future__ import annotations

from backend.infra.integrations.agent_runner import _unauthorized_order_refusal

ORDER = "262534700000036039"
OTHER = "262534600000036983"


def _cancel(order_id: str, code: str = "601869") -> dict[str, object]:
    return {"instruction": f"撤单 {order_id} {code}"}


def test_stages_that_are_not_a_watch_are_untouched() -> None:
    """Only the watch passes an authorization set; everything else passes None."""

    refusal = _unauthorized_order_refusal(
        object(), _cancel(ORDER), authorized=None
    )

    assert refusal is None


def test_no_plan_at_all_forbids_every_write() -> None:
    """The agreed rule, kept in code rather than in a prompt.

    An empty set says a plan was read and named nothing, which is a decision.
    A capable model can reason its way past a sentence; it cannot reason its
    way past never being handed the call.
    """

    refusal = _unauthorized_order_refusal(
        object(), _cancel(ORDER), authorized=frozenset()
    )

    assert refusal is not None
    assert "没有可执行的挂单处置计划" in refusal


def test_an_order_the_plan_names_may_be_cancelled() -> None:
    assert (
        _unauthorized_order_refusal(
            object(), _cancel(ORDER), authorized=frozenset({ORDER})
        )
        is None
    )


def test_an_order_the_plan_never_mentioned_may_not_be() -> None:
    refusal = _unauthorized_order_refusal(
        object(), _cancel(OTHER), authorized=frozenset({ORDER})
    )

    assert refusal is not None
    assert OTHER in refusal


def test_a_blanket_cancel_is_refused_however_full_the_plan_is() -> None:
    """It carries no order id, so there is nothing to check it against.

    Its effect is to withdraw every resting order — including the ones the
    plan explicitly said to keep — so no plan could authorize it.
    """

    refusal = _unauthorized_order_refusal(
        object(),
        {"instruction": "一键撤单"},
        authorized=frozenset({ORDER, OTHER}),
    )

    assert refusal is not None
    assert "一键撤单" in refusal
