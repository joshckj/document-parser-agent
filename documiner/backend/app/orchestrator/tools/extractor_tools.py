"""
Tools for the inner Extractor agent.

These are session-bound: `get_extractor_tools(session_id)` returns tools that
close over the session id and read the cached OCR JSON from `session_store`
(the same binding-at-construction pattern the example uses for DB-bound tools).

The Extractor agent uses them to:
  - list_block_labels() : discover which block types the document contains
  - get_blocks(label)   : pull the blocks of a chosen label back as an HTML
                          fragment (also cached as the session "extracted"
                          scratch so the subagent node can package it)
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from typing import Any, Dict, List

from langchain_core.tools import tool

from orchestrator import session_store


# ---------------------------------------------------------------------------
# OCR JSON navigation
# ---------------------------------------------------------------------------

def _parsing_list(payload: Any) -> List[Dict[str, Any]]:
    """Return GROUNDING.parsing_res_list, tolerant of the /upload wrapper."""
    if not isinstance(payload, dict):
        return []
    doc = payload.get("document") if "document" in payload else payload
    grounding = (doc or {}).get("GROUNDING") or {}
    blocks = grounding.get("parsing_res_list")
    return blocks if isinstance(blocks, list) else []


def _normalize_label(label: str) -> str:
    label = (label or "").strip().lower()
    # tolerate plurals: "tables" -> "table", "images" -> "image"
    if label.endswith("s") and len(label) > 1:
        label = label[:-1]
    return label


def _matching_blocks(blocks: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    target = _normalize_label(label)
    return [b for b in blocks if _normalize_label(b.get("block_label", "")) == target]


# ---------------------------------------------------------------------------
# block_content -> HTML
# ---------------------------------------------------------------------------

_HTML_HINTS = ("<table", "<div", "<img", "<ul", "<ol", "<p", "<h")


def _looks_like_html(text: str) -> bool:
    low = text.lower()
    return any(hint in low for hint in _HTML_HINTS)


def _looks_like_md_table(text: str) -> bool:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    # header row + separator row of dashes/pipes
    return "|" in lines[0] and bool(re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[1]))


def _md_table_to_html(text: str) -> str:
    rows = [ln for ln in text.strip().splitlines() if ln.strip()]
    def cells(line: str) -> List[str]:
        line = line.strip().strip("|")
        return [c.strip() for c in line.split("|")]

    header = cells(rows[0])
    body = [cells(r) for r in rows[2:]]  # skip header + separator

    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{_html.escape(c)}</th>" for c in header]
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>" + "".join(f"<td>{_html.escape(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _block_to_html(content: str) -> str:
    content = content or ""
    if _looks_like_html(content):
        return content  # OCR already gave us HTML — pass through
    if _looks_like_md_table(content):
        return _md_table_to_html(content)
    # plain text / title — preserve as an escaped block
    return f'<div class="block-text">{_html.escape(content)}</div>'


def _blocks_to_html(blocks: List[Dict[str, Any]]) -> str:
    parts = [_block_to_html(b.get("block_content", "")) for b in blocks]
    return '<div class="extracted-blocks">' + "\n".join(parts) + "</div>"


# ---------------------------------------------------------------------------
# Tool factory (session-bound)
# ---------------------------------------------------------------------------

def get_extractor_tools(session_id: str) -> list:
    """Return list_block_labels / get_blocks tools bound to this session."""

    @tool
    def list_block_labels() -> str:
        """List the distinct block types (block_label) present in the parsed
        document, with a count of each. Call this first to see what is available
        (e.g. table, image, text, paragraph_title) before extracting."""
        payload = session_store.get_last_ocr(session_id)
        blocks = _parsing_list(payload)
        if not blocks:
            return (
                "No parsed document is available for this session. Ask the user to "
                "upload a document first."
            )
        counts = Counter(_normalize_label(b.get("block_label", "")) for b in blocks)
        inventory = ", ".join(f"{label} ({n})" for label, n in sorted(counts.items()))
        return f"Available block types: {inventory}"

    @tool
    def get_blocks(block_label: str) -> str:
        """Extract every block of the given block_label from the parsed document
        and return it as an HTML fragment. Use the label names from
        list_block_labels (e.g. 'table', 'image'). Returns a note if none match."""
        payload = session_store.get_last_ocr(session_id)
        blocks = _parsing_list(payload)
        if not blocks:
            return "No parsed document is available for this session."
        matched = _matching_blocks(blocks, block_label)
        if not matched:
            return f"No blocks with label '{block_label}' were found in this document."
        fragment = _blocks_to_html(matched)
        # Hand the authoritative HTML back to the subagent node via the store,
        # so the node packages it rather than trusting the LLM to copy it.
        session_store.set_extracted(session_id, fragment)
        return fragment

    return [list_block_labels, get_blocks]
