"""Flattening branch-shaped parameter schemas into one a model can read.

A ``oneOf`` at the top of a tool's parameter schema carries no ``properties``
of its own.  Providers that do not expand it hand the model an object with no
declared fields at all, and the model then invents them — this repository's
traces hold 371 such calls, including two tools that never once succeeded.

Merging the branches keeps every field visible.  What the branches encoded and
a flat object cannot — "these fields are required only for that action" —
moves into the discriminator's description, which the model does see.  The
runtime check in each tool remains the real enforcement, as it always was.
"""

from __future__ import annotations

from typing import Any, cast

from backend.llm import ProviderJsonObject

# Keys that say what a value is. The first branch to mention one wins: branches
# differ in how much they allow, not in what the field means.
_DESCRIPTIVE_KEYS = ("type", "description", "default", "items", "pattern", "format")

# Keys that say how much a value is allowed to be, and how to widen two of them.
_LOWER_BOUNDS = ("minimum", "minItems", "minLength")
_UPPER_BOUNDS = ("maximum", "maxItems", "maxLength")


def _json_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _without_const(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite ``{"const": x}`` as a one-value ``enum`` so branches can merge."""

    if "const" not in schema:
        return dict(schema)
    value = schema["const"]
    rewritten = {key: item for key, item in schema.items() if key != "const"}
    rewritten.setdefault("type", _json_type(value))
    rewritten["enum"] = [value]
    return rewritten


def _merge_property(versions: list[dict[str, Any]]) -> dict[str, Any]:
    """One schema accepting everything any branch accepted for this field.

    A constraint survives only when every branch imposed it. One branch
    allowing ``page`` up to 100 and another pinning it to 1 must merge into the
    permissive reading, or the model is told it cannot do something it can.
    """

    merged: dict[str, Any] = {}
    for key in _DESCRIPTIVE_KEYS:
        for version in versions:
            if key in version:
                merged[key] = version[key]
                break

    if all("enum" in version for version in versions):
        values: list[Any] = []
        for version in versions:
            for item in version["enum"]:
                if item not in values:
                    values.append(item)
        merged["enum"] = values

    for key in _LOWER_BOUNDS:
        if all(key in version for version in versions):
            merged[key] = min(version[key] for version in versions)
    for key in _UPPER_BOUNDS:
        if all(key in version for version in versions):
            merged[key] = max(version[key] for version in versions)
    return merged


def _discriminators(branches: list[dict[str, Any]]) -> list[str]:
    """Fields every branch pins to a constant — what selects the branch."""

    shared: set[str] | None = None
    for branch in branches:
        pinned = {
            name
            for name, schema in branch.get("properties", {}).items()
            if isinstance(schema, dict) and "const" in schema
        }
        shared = pinned if shared is None else shared & pinned
    return sorted(shared or ())


def _branch_hint(
    branches: list[dict[str, Any]],
    discriminators: list[str],
    always_required: list[str],
) -> str | None:
    """Describe each branch's fields, which a flat schema cannot express.

    Both halves matter. Without the required half the model omits fields;
    without the optional half it sends a field to an action that has no place
    for it, and the request object rejects the call. Fields the merged schema
    already requires are left out — repeating them teaches nothing.

    Branches sharing a discriminator value are folded together: several tools
    split one action further on a second field, and listing that action twice
    with different requirements reads as a contradiction. Folded, a field is
    described as required only when every branch of that action demands it;
    the tool's own check still refuses the combinations this cannot express.
    """

    covered = set(discriminators) | set(always_required)
    grouped: dict[str, list[tuple[list[str], list[str]]]] = {}
    for branch in branches:
        properties = branch.get("properties", {})
        label = "、".join(
            f"{name}={properties[name]['const']}"
            for name in discriminators
            if name in properties and "const" in properties[name]
        )
        if not label:
            continue
        required = [
            field for field in branch.get("required", []) if field not in covered
        ]
        mentioned = [field for field in properties if field not in covered]
        grouped.setdefault(label, []).append((required, mentioned))

    sentences: list[str] = []
    shapes: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for label, variants in grouped.items():
        required = [
            field
            for field in variants[0][0]
            if all(field in other[0] for other in variants)
        ]
        optional: list[str] = []
        for variant_required, mentioned in variants:
            # A field only some variants demand is optional for the action as
            # a whole, so it belongs here rather than above.
            for field in (*mentioned, *variant_required):
                if field not in required and field not in optional:
                    optional.append(field)
        shapes.add((tuple(required), tuple(optional)))
        parts = []
        if required:
            parts.append(f"必填 {'、'.join(required)}")
        if optional:
            parts.append(f"可选 {'、'.join(optional)}")
        sentences.append(
            f"{label} 时{'，'.join(parts)}" if parts else f"{label} 时无其他参数"
        )

    # Every branch taking the same fields means the merged schema already says
    # everything there is to say.
    if len(shapes) <= 1:
        return None
    return "；".join(sentences)


def merge_branches(branches: list[ProviderJsonObject]) -> ProviderJsonObject:
    """Collapse ``oneOf`` branches into a single readable object schema."""

    plain = [cast(dict[str, Any], branch) for branch in branches]
    if not plain:
        raise ValueError("a tool schema needs at least one branch")
    if len(plain) == 1:
        return cast(ProviderJsonObject, dict(plain[0]))

    versions: dict[str, list[dict[str, Any]]] = {}
    for branch in plain:
        for name, schema in branch.get("properties", {}).items():
            versions.setdefault(name, []).append(_without_const(schema))

    properties = {name: _merge_property(items) for name, items in versions.items()}

    # Only a field every branch demands can be demanded of the merged schema.
    required = [
        field
        for field in plain[0].get("required", [])
        if all(field in branch.get("required", []) for branch in plain)
    ]

    discriminators = _discriminators(plain)
    hint = _branch_hint(plain, discriminators, required)
    if hint and discriminators:
        target = properties[discriminators[0]]
        existing = target.get("description")
        target["description"] = f"{existing} {hint}" if existing else hint

    return cast(
        ProviderJsonObject,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    )


__all__ = ["merge_branches"]
