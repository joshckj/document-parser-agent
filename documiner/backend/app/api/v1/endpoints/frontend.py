import os
import importlib.util
from pathlib import Path
from functools import lru_cache
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.config import settings


router = APIRouter()

PARSE_BASE = os.getenv("DOCUMINER_PARSE_BASE", "http://localhost:3000")
TIMEOUT = 60.0
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ORCHESTRATOR_FILE = Path(__file__).resolve().parents[3] / "orchestrator" / "agent " / "orchestrator.py"


class ChatMessage(BaseModel):
	role: str
	content: str


class ChatRequest(BaseModel):
	messages: list[ChatMessage]


@router.post("/upload")
async def upload_document(
	file: UploadFile = File(..., description="Document file to parse."),
	message: str | None = Form(default=None),
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

	return {
		"message": _extract_primary_text(payload),
		"document": payload,
		"filename": filename,
	}


@router.post("/chat")
async def chat_with_document(request: ChatRequest) -> dict:
	"""Send a plain messages payload to the simple deep agent."""

	messages = [message.model_dump() for message in request.messages if message.content.strip()]
	if not messages:
		raise HTTPException(status_code=400, detail="At least one message is required.")

	assistant_message = await _get_deep_agent().invoke_deep_agent(
		messages=messages,
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
) -> Any:
	"""Proxy the upload to the parser backend and return its response payload."""

	try:
		async with httpx.AsyncClient(timeout=TIMEOUT) as client:
			response = await client.post(
				f"{PARSE_BASE}/chat",
				data={"message": message or ""},
				files={"file": (filename, file_bytes, content_type)},
			)
			response.raise_for_status()
	except httpx.HTTPStatusError as exc:
		detail = exc.response.text or "Parser backend returned an error."
		raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
	except httpx.HTTPError as exc:
		raise HTTPException(
			status_code=502,
			detail=f"Unable to reach parser backend at {PARSE_BASE}/chat",
		) from exc

	if "application/json" in response.headers.get("content-type", ""):
		payload = response.json()
		return payload

	return {"message": response.text}


def _extract_primary_text(payload: Any) -> str:
	if isinstance(payload, str):
		return payload
	if isinstance(payload, dict):
		for key in ("message", "text", "content", "summary"):
			value = payload.get(key)
			if isinstance(value, str) and value.strip():
				return value
	return "Document uploaded successfully."
@lru_cache(maxsize=1)
def _get_deep_agent() -> Any:
	spec = importlib.util.spec_from_file_location("documiner_deep_agent", ORCHESTRATOR_FILE)
	if spec is None or spec.loader is None:
		raise RuntimeError(f"Unable to load orchestrator from {ORCHESTRATOR_FILE}")
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


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
