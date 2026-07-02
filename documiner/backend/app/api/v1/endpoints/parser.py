import os

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile


router = APIRouter()

PARSE_BASE = os.getenv("DOCUMINER_PARSE_BASE", "https://sp-doc-insight.qa.in.spdigital.sg/ocr/image")
TIMEOUT = 60.0


@router.post("/data")
async def upload_data(
    gas_leak_file: UploadFile = File(..., description="DMIS gas leak incidents CSV"),
    pipe_data_file: UploadFile = File(..., description="Pipe network data CSV"),
) -> dict:
    """Proxy CSV uploads to the parser backend."""

    gas_leak_bytes = await gas_leak_file.read()
    pipe_bytes = await pipe_data_file.read()
    if not gas_leak_bytes or not pipe_bytes:
        raise HTTPException(status_code=400, detail="Both CSV files are required.")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{PARSE_BASE}/data",
                files={
                    "gas_leak_file": (gas_leak_file.filename or "gas_leak.csv", gas_leak_bytes, "text/csv"),
                    "pipe_data_file": (pipe_data_file.filename or "pipe_data.csv", pipe_bytes, "text/csv"),
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "Parser backend returned an error."
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to reach parser backend at {PARSE_BASE}/data",
        ) from exc

    if "application/json" in response.headers.get("content-type", ""):
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"message": str(payload)}

    return {"message": response.text}