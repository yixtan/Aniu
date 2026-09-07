"""Turn a run report into an email body that survives mail clients.

The Summary stage emits Markdown with raw HTML interleaved, and the web page
renders it with remark plus rehype-raw. markdown-it-py follows the same
CommonMark rules, so an email built here matches what the page shows —
including the cases CommonMark leaves alone, such as Markdown inside a raw HTML
block.
"""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt

MAX_CONTENT_WIDTH_PX = 720
"""Mail clients render at the window width unless a container constrains them."""

_MARKDOWN = MarkdownIt("commonmark", {"html": True}).enable("table")

_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',sans-serif"
)

# Mail clients apply their own defaults to bare tags and many strip <style>,
# so the tags the Markdown conversion produces get inline styles instead. The
# report's own markup already carries inline styles and is left untouched.
_BLOCK_STYLES: dict[str, str] = {
    "h1": "font-size:21px;font-weight:700;margin:22px 0 10px;line-height:1.4",
    "h2": "font-size:18px;font-weight:700;margin:20px 0 9px;line-height:1.4",
    "h3": "font-size:16px;font-weight:600;margin:18px 0 8px;line-height:1.45",
    "h4": "font-size:15px;font-weight:600;margin:16px 0 7px;line-height:1.45",
    "p": "margin:9px 0;line-height:1.75",
    "ul": "margin:9px 0;padding-inline-start:22px",
    "ol": "margin:9px 0;padding-inline-start:22px",
    "li": "margin:4px 0;line-height:1.7",
    "blockquote": (
        "margin:12px 0;padding:8px 12px;border-inline-start:3px solid #d0d7de;"
        "color:#57606a;background:#f6f8fa"
    ),
    "table": "border-collapse:collapse;width:100%;margin:12px 0;font-size:13px",
    "th": (
        "border:1px solid #d0d7de;padding:6px 8px;background:#f6f8fa;"
        "text-align:start"
    ),
    "td": "border:1px solid #d0d7de;padding:6px 8px",
    "pre": (
        "margin:12px 0;padding:10px 12px;background:#f6f8fa;border-radius:6px;"
        "overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-size:12px"
    ),
    "hr": "border:0;border-top:1px solid #e5e7eb;margin:18px 0",
}
_UNSTYLED_TAG = re.compile(
    r"<(?P<tag>" + "|".join(_BLOCK_STYLES) + r")(?P<attrs>\s[^>]*)?>",
    flags=re.IGNORECASE,
)


def _apply_block_styles(markup: str) -> str:
    """Give generated block tags inline styles, leaving styled tags alone."""

    def replace(match: re.Match[str]) -> str:
        tag = match.group("tag").lower()
        attrs = match.group("attrs") or ""
        if "style=" in attrs.lower():
            return match.group(0)
        return f'<{tag}{attrs} style="{_BLOCK_STYLES[tag]}">'

    return _UNSTYLED_TAG.sub(replace, markup)


def render_report_email(summary: str, render_mode: str) -> str:
    """Return the full HTML document to use as the email body."""

    if render_mode == "html":
        content = _apply_block_styles(_MARKDOWN.render(summary))
    else:
        # A degraded run yields plain Markdown. Escaping it into a preformatted
        # block keeps it readable instead of shipping markup nothing renders.
        content = (
            f'<pre style="{_BLOCK_STYLES["pre"]}">{html.escape(summary)}</pre>'
        )
    return (
        '<div style="margin:0;padding:16px 0;background:#f0f2f5;">'
        f'<div style="max-width:{MAX_CONTENT_WIDTH_PX}px;margin:0 auto;'
        'background:#ffffff;border-radius:8px;padding:20px 24px;'
        f"font-family:{_FONT_STACK};font-size:14px;line-height:1.7;"
        'color:#1f2328;word-break:break-word;">'
        f"{content}"
        "</div></div>"
    )


__all__ = ["MAX_CONTENT_WIDTH_PX", "render_report_email"]
