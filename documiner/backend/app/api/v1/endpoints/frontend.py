import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.config import settings
from orchestrator import session_store
from orchestrator.agent.orchestrator import invoke_deep_agent


router = APIRouter()

OCR_BASE = os.getenv("OCR_BASE", "https://sp-doc-insight.qa.in.spdigital.sg")
TIMEOUT = 60.0
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


class ChatMessage(BaseModel):
	role: str
	content: str


class ChatRequest(BaseModel):
	messages: list[ChatMessage]
	session_id: str | None = None


@router.post("/upload")
async def upload_document(
	file: UploadFile = File(..., description="Document file to parse."),
	message: str | None = Form(default=None),
	session_id: str | None = Form(default=None),
) -> dict:
	"""Upload a document and proxy it to the parser backend."""

	filename = file.filename or "document"
	extension = Path(filename).suffix.lower()
	if extension not in SUPPORTED_EXTENSIONS:
		raise HTTPException(
			status_code=400,
			detail="Accepted formats: pdf, jpg, jpeg, png, webp.",
		)

	file_bytes = await file.read()
	if not file_bytes:
		raise HTTPException(status_code=400, detail="Uploaded file is empty.")

	content_type = file.content_type or _guess_content_type(extension)
	payload = await _proxy_upload(filename, file_bytes, content_type, message)

	# Cache the full OCR JSON so the chat Extractor subagent can inspect it.
	if session_id:
		session_store.set_last_ocr(session_id, payload)

	return {
		"message": _extract_primary_text(payload),
		"document": payload,
		"filename": filename,
		"session_id": session_id,
	}


@router.post("/chat")
async def chat_with_document(request: ChatRequest) -> dict:
	"""Send a plain messages payload to the orchestrator agent."""

	messages = [message.model_dump() for message in request.messages if message.content.strip()]
	if not messages:
		raise HTTPException(status_code=400, detail="At least one message is required.")

	assistant_message = await invoke_deep_agent(
		messages=messages,
		session_id=request.session_id or "",
		agent_model=settings.agent_model,
		agent_key=settings.agent_key,
		agent_base_url=settings.agent_base_url,
	)

	return {
		"message": assistant_message,
		"messages": [*messages, {"role": "assistant", "content": assistant_message}],
	}


async def _proxy_upload(
	filename: str,
	file_bytes: bytes,
	content_type: str,
	message: str | None,
	effort: str = "low",
) -> Any:
	"""Call the SP Doc Insight OCR API and return its response payload."""

	try:
		async with httpx.AsyncClient(timeout=TIMEOUT) as client:
			response = await client.post(
				f"{OCR_BASE}/ocr/image",
				params={"effort": effort},
				headers={"accept": "application/json"},
				files={"file": (filename, file_bytes, content_type)},
			)
			response.raise_for_status()
	except httpx.HTTPStatusError as exc:
		detail = exc.response.text or "OCR service returned an error."
		raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
	except httpx.HTTPError as exc:
		raise HTTPException(
			status_code=502,
			detail=f"Unable to reach OCR service at {OCR_BASE}/ocr/image",
		) from exc

	if "application/json" in response.headers.get("content-type", ""):
		payload = response.json()
		return payload

	return {"message": response.text}


def _extract_primary_text(payload: Any) -> str:
	if isinstance(payload, str):
		return payload
	if isinstance(payload, dict):
		for key in ("MD", "message", "text", "content", "summary"):
			value = payload.get(key)
			if isinstance(value, str) and value.strip():
				return value
	return "Document uploaded successfully."


def _guess_content_type(extension: str) -> str:
	if extension == ".pdf":
		return "application/pdf"
	if extension in {".jpg", ".jpeg"}:
		return "image/jpeg"
	if extension == ".png":
		return "image/png"
	if extension == ".webp":
		return "image/webp"
	return "application/octet-stream"
