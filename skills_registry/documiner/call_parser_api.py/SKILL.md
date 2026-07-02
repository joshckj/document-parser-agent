---
name: call-ocr-api
description: Load before calling the call_ocr_api tool.
---

# call-ocr-api

## Overview

Use the `call_ocr_api` tool to send an image or PDF to the SP Document Insight OCR service
and receive the extracted markdown text plus document metadata (image quality, PII detection,
dimensions). Use the metadata to give the user a short **parsing summary** after every parse.

## Endpoint

```
POST https://sp-doc-insight.qa.in.spdigital.sg/ocr/image?effort={effort}
Content-Type: multipart/form-data
Accept: application/json

file: <binary file upload>
```

## When to use

- User asks to parse, extract, or read content from an image or PDF.
- User uploads a file and wants its text extracted.
- User references a local file path and wants OCR performed on it.

## Parameters

| Parameter   | Required | Values                    | Description                                       |
|-------------|----------|---------------------------|---------------------------------------------------|
| `file_path` | Yes      | string                    | Local path to the file (PNG, JPG, JPEG, WEBP, PDF)|
| `effort`    | No       | `low` / `medium` / `max`  | Parsing depth. Default: `low`                     |

## Effort levels

| Level    | Speed    | Use case                                        |
|----------|----------|-------------------------------------------------|
| `low`    | Fast     | Simple documents, plain text, quick extraction  |
| `medium` | Moderate | Mixed content with tables or images             |
| `max`    | Thorough | Complex layouts, charts, seals, multi-column    |

## Response schema

The OCR service returns JSON with three top-level keys:

| Key         | Description                                                                 |
|-------------|-----------------------------------------------------------------------------|
| `MD`        | Extracted markdown text — the main content.                                 |
| `GROUNDING` | Layout details: page `width`/`height`, per-block bounding boxes, model settings. |
| `NOTES`     | Run metadata used for the parsing summary (see below).                      |

The `call_ocr_api` tool itself returns the `MD` string. The full JSON (including `NOTES`) is
what the `/upload` endpoint returns to the frontend as `{ message, document, filename }`.

### Fields to read for the summary

All of these live under `NOTES`:

| Field                                    | Example                        | Summary line     |
|------------------------------------------|--------------------------------|------------------|
| *(the uploaded filename)*                | `invoice.png`                  | **Image name**   |
| `NOTES.pii_detector.pii_detected`        | `"FALSE"` / `"TRUE"`           | **PII detected** |
| `NOTES.image_preprocessing.comments`     | `"Clarity: Clear (Not blurry)"`| **Clarity**      |
| `NOTES.image_preprocessing.image_dims`   | `"1700x2200"`                  | **Size**         |
| `NOTES.image_preprocessing.blur_score`   | `1573.58`                      | (backs Clarity)  |
| `NOTES.effort`                           | `"low"`                        | **Effort**       |
| `NOTES.parsing_duration_ms`              | `10577`                        | **Parse time**   |

If `image_dims` is missing, fall back to `GROUNDING.width` × `GROUNDING.height`.
Treat `pii_detected` case-insensitively: only `"TRUE"` means PII was found.

## Producing the summary

After a successful parse, reply with a short confirmation followed by the key facts, e.g.:

```
Document parsing completed.

- Image name: invoice.png
- PII detected: No
- Clarity: Clear (Not blurry)
- Size: 1700x2200
- Effort: low
- Parse time: 10.6s
```

Then offer the extracted content (`MD`) if the user wants it. Keep the summary compact —
do not dump the raw `GROUNDING` blocks or bounding boxes into the chat.

## Example invocation

```python
result = await call_ocr_api(file_path="/tmp/invoice.png", effort="low")
```

Equivalent curl:
```bash
curl -X POST 'https://sp-doc-insight.qa.in.spdigital.sg/ocr/image?effort=low' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@invoice.png;type=image/png'
```

## Reference

See [references/document_parser_service.md](references/document_parser_service.md) for a full
annotated example of the response JSON.
