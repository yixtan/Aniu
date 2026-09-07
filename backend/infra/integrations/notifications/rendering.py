"""Shared payload rendering for outbound push notifications."""

from __future__ import annotations

import json
import re

from backend.business.notifications import TradeNotificationEvent

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<key>[a-z_][a-z0-9_]*)\s*\}\}")
MAX_RENDERED_BODY_BYTES = 64_000


def _escaped(value: object) -> str:
    """Escape a value for substitution inside a JSON string literal."""

    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False)[1:-1]


def render_body_template(template: str, event: TradeNotificationEvent) -> object:
    """Substitute ``{{field}}`` placeholders and parse the result as JSON.

    Values are escaped for a JSON string context, so a template writes
    ``{"text": "{{title}}"}`` and multi-line content stays valid JSON.
    """

    mapping = event.as_mapping()

    def substitute(match: re.Match[str]) -> str:
        key = match.group("key")
        if key not in mapping:
            raise ValueError(f"unknown notification template field: {key}")
        return _escaped(mapping[key])

    rendered = _PLACEHOLDER.sub(substitute, template)
    try:
        return json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ValueError(f"body template did not render valid JSON: {exc}") from exc


def default_body(event: TradeNotificationEvent) -> dict[str, object]:
    return event.as_mapping()


__all__ = [
    "MAX_RENDERED_BODY_BYTES",
    "default_body",
    "render_body_template",
]
