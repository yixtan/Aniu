"""Flattening branch-shaped tool schemas into one the model can read."""

from __future__ import annotations

from typing import Any, cast

import pytest

from backend.infra.integrations.tool_schema import merge_branches


def branch(properties: dict[str, Any], required: list[str]) -> Any:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def test_the_merged_schema_declares_every_field() -> None:
    """The whole point: a branch-shaped schema reaches the model with none."""

    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch({"op": {"const": "a"}, "x": {"type": "string"}}, ["op", "x"]),
                branch({"op": {"const": "b"}, "y": {"type": "integer"}}, ["op", "y"]),
            ]
        ),
    )

    assert "oneOf" not in merged
    assert set(merged["properties"]) == {"op", "x", "y"}
    assert merged["additionalProperties"] is False


def test_branch_constants_become_one_enum() -> None:
    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch({"op": {"const": "create"}}, ["op"]),
                branch({"op": {"const": "update"}}, ["op"]),
                branch({"op": {"const": "delete"}}, ["op"]),
            ]
        ),
    )

    assert merged["properties"]["op"]["enum"] == ["create", "update", "delete"]
    assert merged["properties"]["op"]["type"] == "string"


def test_only_a_field_every_branch_demands_stays_required() -> None:
    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch({"op": {"const": "a"}, "x": {"type": "string"}}, ["op", "x"]),
                branch({"op": {"const": "b"}, "x": {"type": "string"}}, ["op"]),
            ]
        ),
    )

    # x is required for one branch only, so requiring it outright would refuse
    # a call the tool accepts.
    assert merged["required"] == ["op"]


def test_a_bound_survives_only_when_every_branch_imposes_it() -> None:
    """One branch pinning a value must not narrow another that does not."""

    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch(
                    {"op": {"const": "a"}, "page": {"const": 1, "default": 1}}, ["op"]
                ),
                branch(
                    {
                        "op": {"const": "b"},
                        "page": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    ["op"],
                ),
            ]
        ),
    )

    page = merged["properties"]["page"]
    assert "enum" not in page
    assert "maximum" not in page


def test_numeric_bounds_widen_to_admit_every_branch() -> None:
    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch(
                    {
                        "d": {"const": "x"},
                        "n": {"type": "integer", "minimum": 5, "maximum": 20},
                    },
                    ["d"],
                ),
                branch(
                    {
                        "d": {"const": "y"},
                        "n": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                    ["d"],
                ),
            ]
        ),
    )

    assert merged["properties"]["n"]["minimum"] == 1
    assert merged["properties"]["n"]["maximum"] == 60


def test_enums_union_when_every_branch_has_one() -> None:
    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch(
                    {"d": {"const": "x"}, "m": {"type": "string", "enum": ["a"]}}, ["d"]
                ),
                branch(
                    {"d": {"const": "y"}, "m": {"type": "string", "enum": ["a", "b"]}},
                    ["d"],
                ),
            ]
        ),
    )

    assert merged["properties"]["m"]["enum"] == ["a", "b"]


def test_the_discriminator_carries_what_each_branch_needs() -> None:
    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch(
                    {"op": {"const": "create"}, "content": {"type": "string"}},
                    ["op", "content"],
                ),
                branch(
                    {"op": {"const": "delete"}, "id": {"type": "integer"}}, ["op", "id"]
                ),
            ]
        ),
    )

    hint = merged["properties"]["op"]["description"]
    assert "op=create 时必填 content" in hint
    assert "op=delete 时必填 id" in hint


def test_optional_fields_are_named_too() -> None:
    """A field sent to the wrong action is refused, so say where it belongs."""

    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch(
                    {"op": {"const": "a"}, "page": {"type": "integer"}},
                    ["op"],
                ),
                branch(
                    {"op": {"const": "b"}, "who": {"type": "string"}}, ["op", "who"]
                ),
            ]
        ),
    )

    hint = merged["properties"]["op"]["description"]
    assert "op=a 时可选 page" in hint
    assert "op=b 时必填 who" in hint


def test_branches_sharing_a_value_are_folded_into_one_sentence() -> None:
    """Listing one action twice with different requirements reads as a lie."""

    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch({"op": {"const": "f"}, "mode": {"const": "latest"}}, ["op"]),
                branch(
                    {
                        "op": {"const": "f"},
                        "mode": {"const": "quarterly"},
                        "page": {"type": "integer"},
                    },
                    ["op", "mode"],
                ),
                branch({"op": {"const": "g"}}, ["op"]),
            ]
        ),
    )

    hint = merged["properties"]["op"]["description"]
    assert hint.count("op=f") == 1
    # mode is demanded by only one of the two f branches, so it is not required.
    assert "op=f 时可选 mode、page" in hint


def test_no_hint_when_every_branch_takes_the_same_fields() -> None:
    """A hint that repeats the schema costs tokens and teaches nothing."""

    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch({"d": {"const": "x"}, "s": {"type": "string"}}, ["d", "s"]),
                branch({"d": {"const": "y"}, "s": {"type": "string"}}, ["d", "s"]),
            ]
        ),
    )

    assert "description" not in merged["properties"]["d"]


def test_an_existing_description_is_kept_alongside_the_hint() -> None:
    merged = cast(
        dict[str, Any],
        merge_branches(
            [
                branch(
                    {
                        "op": {"const": "a", "description": "要做的事。"},
                        "x": {"type": "string"},
                    },
                    ["op", "x"],
                ),
                branch({"op": {"const": "b"}}, ["op"]),
            ]
        ),
    )

    description = merged["properties"]["op"]["description"]
    assert description.startswith("要做的事。")
    assert "op=a 时必填 x" in description


def test_a_single_branch_passes_through_unchanged() -> None:
    only = branch({"x": {"type": "string"}}, ["x"])

    assert merge_branches([only]) == only


def test_no_branches_at_all_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="at least one branch"):
        merge_branches([])
