import mimetypes
import os
from typing import Literal, Optional

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from orchestrator import session_store

OCR_BASE = "https://sp-doc-insight.qa.in.spdigital.sg"
TIMEOUT = 120.0


@tool
async def call_ocr_api(
    file_path: str,
    effort: Literal["low", "medium", "max"] = "low",
    config: Optional[RunnableConfig] = None,
) -> str:
    """
    Extract text and structure from a document using the SP Document Insight OCR API.

    Sends the file at file_path to the OCR service and returns the extracted
    markdown text.

    Args:
        file_path: Absolute or relative path to the image or PDF to parse.
        effort: Parsing effort level — "low" is fastest, "max" is most thorough.

    Returns:
        Extracted markdown text from the document.
    """
    if not os.path.isfile(file_path):
        return f"Error: file not found at {file_path!r}"

    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or "application/octet-stream"
    filename = os.path.basename(file_path)

    with open(file_path, "rb") as fh:
        file_bytes = fh.read()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{OCR_BASE}/ocr/image",
            params={"effort": effort},
            headers={"accept": "application/json"},
            files={"file": (filename, file_bytes, mime_type)},
        )
        response.raise_for_status()

    data = response.json()

    # Cache the full JSON so the Extractor subagent can inspect block_labels.
    session_id = (config or {}).get("configurable", {}).get("thread_id", "") if config else ""
    if session_id:
        session_store.set_last_ocr(session_id, data)

    return data.get("MD") or str(data)
