# Document Parser Service

**URL:** https://sp-doc-insight.qa.in.spdigital.sg

This is a reference for the `/ocr/image` response so the orchestrator knows what fields are
available when parsing a document. See [../SKILL.md](../SKILL.md) for how to call the tool and
how to build the parsing summary.

## Response shape

```jsonc
{
  "MD": "…extracted markdown text…",   // main content — this is what call_ocr_api returns

  "GROUNDING": {
    "width": 1700,                      // page width in px (fallback for Size)
    "height": 2200,                     // page height in px
    "model_settings": { … },
    "parsing_res_list": [ … ],          // per-block content + bounding boxes (verbose — do not dump)
    "layout_det_res": { … }
  },

  "NOTES": {
    "effort": "low",                    // → Effort
    "models_used": ["paddleocr-vl-1.6"],
    "score": "NA",
    "needs_review": "NA",
    "parsing_duration_ms": 10577,       // → Parse time (ms; divide by 1000 for seconds)

    "image_preprocessing": {
      "image_dims": "1700x2200",        // → Size
      "blur_score": 1573.58,            // higher = sharper; backs the clarity call
      "comments": "Clarity: Clear (Not blurry)"   // → Clarity (strip the "Clarity: " prefix)
    },

    "pii_detector": {
      "pii_detected": "FALSE",          // → PII detected  ("TRUE" means PII was found)
      "pii_information": "NA"           // details when PII is present
    }
  }
}
```

## Summary field mapping

| Summary line   | Source                                                        |
|----------------|--------------------------------------------------------------|
| Image name     | uploaded filename                                            |
| PII detected   | `NOTES.pii_detector.pii_detected` (`"TRUE"` → Yes, else No)   |
| Clarity        | `NOTES.image_preprocessing.comments` (strip `Clarity: `)      |
| Size           | `NOTES.image_preprocessing.image_dims` or `GROUNDING.width`×`height` |
| Effort         | `NOTES.effort`                                               |
| Parse time     | `NOTES.parsing_duration_ms` / 1000, in seconds              |

## Notes

- `MD` is the only field the `call_ocr_api` tool returns to the agent; the full JSON above is
  returned by the backend `/upload` endpoint to the frontend as `{ message, document, filename }`.
- `GROUNDING.parsing_res_list` and `layout_det_res` are large — never echo them into the chat.
- Fields such as `score` / `needs_review` may be `"NA"` depending on effort level; treat `"NA"`
  as "not available".
