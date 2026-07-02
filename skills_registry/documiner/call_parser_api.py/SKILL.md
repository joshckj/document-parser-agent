---
name: call-ocr-api
description: Load before calling the call_ocr_api tool.
---

# call-ocr-api

## Overview

Use the `call_ocr_api` tool to send an image or PDF to the SP Document Insight OCR service
and receive the extracted markdown text.

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

| Parameter | Required | Values | Description |
|-----------|----------|--------|-------------|
| `file_path` | Yes | string | Local path to the file (PNG, JPG, JPEG, WEBP, PDF) |
| `effort` | No | `low` / `medium` / `max` | Parsing depth. Default: `low` |

## Effort levels

| Level | Speed | Use case |
|-------|-------|----------|
| `low` | Fast | Simple documents, plain text, quick extraction |
| `medium` | Moderate | Mixed content with tables or images |
| `max` | Thorough | Complex layouts, charts, seals, multi-column |

## Response

The tool returns the `MD` field from the JSON response — extracted markdown text.

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

## When to use

- User asks to parse an image.

## 

{
  "MD": "EBS, delivering greater efficiency and accuracy. Moving forward, CX will focus primarily on reviewing and managing exceptions rather than handling every application manually. ## Key Benefits ■ Improved Efficiency: Streamlined process with reduced manual intervention. ☑ Enhanced Accuracy: Lower risk of errors in move-in handling. Time Savings: Faster turnaround for commercial move-ins. Role Simplification: Enables frontliners to concentrate on exceptions. ### • There is a new platform for customers to submit their cable removal requests. These requests can be done via our Webform. Please do guide / direct customers to use this form when they request for cable removals. You may refer to the screenshots below on the process that customers will go through to submit the request. <div style=\"text-align: center;\"><img src=\"imgs/img_in_image_box_172_894_1517_1497.jpg\" alt=\"Image\" width=\"79%\" /> SPgroup Our Services Sustainable Energy Solutions About Us International Contact Us Online Enquiry Form SP PowerGrid - Worksite Matters Cable/Meter Removal </div>  There is also a selection for permanent or temporary supply which should be selected correctly. Please note that customers must submit an account closure request first before they submit the cable removal request. Exception is only applicable due to the completion of upgrading of supply.",
  "GROUNDING": {
    "input_path": null,
    "page_index": null,
    "page_count": null,
    "width": 1700,
    "height": 2200,
    "model_settings": {
      "use_doc_preprocessor": true,
      "use_layout_detection": true,
      "use_chart_recognition": true,
      "use_seal_recognition": true,
      "use_ocr_for_image_block": true,
      "format_block_content": true,
      "merge_layout_blocks": true,
      "markdown_ignore_labels": [],
      "return_layout_polygon_points": true
    },
    "parsing_res_list": [
      {
        "block_label": "text",
        "block_content": "EBS, delivering greater efficiency and accuracy. Moving forward, CX will focus primarily on reviewing and managing exceptions rather than handling every application manually.",
        "block_bbox": [
          163,
          117,
          1508,
          206
        ],
        "block_id": 0,
        "block_order": 1,
        "group_id": 0,
        "global_block_id": 0,
        "global_group_id": 0,
        "block_polygon_points": [
          [
            163,
            117
          ],
          [
            1508,
            117
          ],
          [
            1508,
            206
          ],
          [
            163,
            206
          ]
        ]
      },
      {
        "block_label": "paragraph_title",
        "block_content": "## Key Benefits",
        "block_bbox": [
          166,
          235,
          359,
          275
        ],
        "block_id": 1,
        "block_order": 2,
        "group_id": 1,
        "global_block_id": 1,
        "global_group_id": 1,
        "block_polygon_points": [
          [
            166,
            235
          ],
          [
            359,
            235
          ],
          [
            359,
            275
          ],
          [
            166,
            275
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "■ Improved Efficiency: Streamlined process with reduced manual intervention.",
        "block_bbox": [
          166,
          306,
          1324,
          346
        ],
        "block_id": 2,
        "block_order": 3,
        "group_id": 2,
        "global_block_id": 2,
        "global_group_id": 2,
        "block_polygon_points": [
          [
            166,
            306
          ],
          [
            1324,
            306
          ],
          [
            1324,
            346
          ],
          [
            166,
            346
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "☑ Enhanced Accuracy: Lower risk of errors in move-in handling.",
        "block_bbox": [
          168,
          351,
          1093,
          391
        ],
        "block_id": 3,
        "block_order": 4,
        "group_id": 3,
        "global_block_id": 3,
        "global_group_id": 3,
        "block_polygon_points": [
          [
            168,
            351
          ],
          [
            1093,
            351
          ],
          [
            1093,
            391
          ],
          [
            168,
            391
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "Time Savings: Faster turnaround for commercial move-ins.",
        "block_bbox": [
          170,
          395,
          1054,
          436
        ],
        "block_id": 4,
        "block_order": 5,
        "group_id": 4,
        "global_block_id": 4,
        "global_group_id": 4,
        "block_polygon_points": [
          [
            170,
            395
          ],
          [
            1054,
            395
          ],
          [
            1054,
            436
          ],
          [
            170,
            436
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "Role Simplification: Enables frontliners to concentrate on exceptions.",
        "block_bbox": [
          171,
          439,
          1211,
          482
        ],
        "block_id": 5,
        "block_order": 6,
        "group_id": 5,
        "global_block_id": 5,
        "global_group_id": 5,
        "block_polygon_points": [
          [
            171,
            439
          ],
          [
            1211,
            439
          ],
          [
            1211,
            482
          ],
          [
            171,
            482
          ]
        ]
      },
      {
        "block_label": "paragraph_title",
        "block_content": "### • There is a new platform for customers to submit their cable removal requests.",
        "block_bbox": [
          222,
          615,
          1470,
          653
        ],
        "block_id": 6,
        "block_order": 7,
        "group_id": 6,
        "global_block_id": 6,
        "global_group_id": 6,
        "block_polygon_points": [
          [
            222,
            615
          ],
          [
            1470,
            615
          ],
          [
            1470,
            653
          ],
          [
            222,
            653
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "These requests can be done via our Webform. Please do guide / direct customers to use this form when they request for cable removals.",
        "block_bbox": [
          169,
          681,
          1497,
          760
        ],
        "block_id": 7,
        "block_order": 8,
        "group_id": 7,
        "global_block_id": 7,
        "global_group_id": 7,
        "block_polygon_points": [
          [
            169,
            681
          ],
          [
            1497,
            681
          ],
          [
            1497,
            760
          ],
          [
            169,
            760
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "You may refer to the screenshots below on the process that customers will go through to submit the request.",
        "block_bbox": [
          170,
          789,
          1548,
          867
        ],
        "block_id": 8,
        "block_order": 9,
        "group_id": 8,
        "global_block_id": 8,
        "global_group_id": 8,
        "block_polygon_points": [
          [
            170,
            789
          ],
          [
            1548,
            789
          ],
          [
            1548,
            867
          ],
          [
            170,
            867
          ]
        ]
      },
      {
        "block_label": "image",
        "block_content": "<div style=\"text-align: center;\"><img src=\"imgs/img_in_image_box_172_894_1517_1497.jpg\" alt=\"Image\" width=\"79%\" />\n\nSPgroup\nOur Services Sustainable Energy Solutions About Us International\nContact Us\nOnline Enquiry Form\nSP PowerGrid - Worksite Matters\nCable/Meter Removal\n\n</div>\n",
        "block_bbox": [
          172,
          894,
          1517,
          1497
        ],
        "block_id": 9,
        "block_order": null,
        "group_id": 9,
        "global_block_id": 9,
        "global_group_id": 9,
        "block_polygon_points": [
          [
            172,
            894
          ],
          [
            1517,
            894
          ],
          [
            1517,
            1497
          ],
          [
            172,
            1497
          ]
        ]
      },
      {
        "block_label": "text",
        "block_content": "There is also a selection for permanent or temporary supply which should be selected correctly. Please note that customers must submit an account closure request first before they submit the cable removal request. Exception is only applicable due to the completion of upgrading of supply.",
        "block_bbox": [
          165,
          1546,
          1524,
          1700
        ],
        "block_id": 10,
        "block_order": 10,
        "group_id": 10,
        "global_block_id": 10,
        "global_group_id": 10,
        "block_polygon_points": [
          [
            165,
            1546
          ],
          [
            1524,
            1546
          ],
          [
            1524,
            1700
          ],
          [
            165,
            1700
          ]
        ]
      }
    ],
    "doc_preprocessor_res": {
      "input_path": null,
      "page_index": null,
      "model_settings": {
        "use_doc_orientation_classify": true,
        "use_doc_unwarping": true
      },
      "angle": 0
    },
    "layout_det_res": {
      "input_path": null,
      "page_index": null,
      "boxes": [
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.9294837117195129,
          "coordinate": [
            163,
            117,
            1508,
            206
          ],
          "order": 1,
          "polygon_points": [
            [
              163,
              117
            ],
            [
              1508,
              117
            ],
            [
              1508,
              206
            ],
            [
              163,
              206
            ]
          ]
        },
        {
          "cls_id": 17,
          "label": "paragraph_title",
          "score": 0.7903233766555786,
          "coordinate": [
            166,
            235,
            359,
            275
          ],
          "order": 2,
          "polygon_points": [
            [
              166,
              235
            ],
            [
              359,
              235
            ],
            [
              359,
              275
            ],
            [
              166,
              275
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.85430908203125,
          "coordinate": [
            166,
            306,
            1324,
            346
          ],
          "order": 3,
          "polygon_points": [
            [
              166,
              306
            ],
            [
              1324,
              306
            ],
            [
              1324,
              346
            ],
            [
              166,
              346
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.838495135307312,
          "coordinate": [
            168,
            351,
            1093,
            391
          ],
          "order": 4,
          "polygon_points": [
            [
              168,
              351
            ],
            [
              1093,
              351
            ],
            [
              1093,
              391
            ],
            [
              168,
              391
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.8377862572669983,
          "coordinate": [
            170,
            395,
            1054,
            436
          ],
          "order": 5,
          "polygon_points": [
            [
              170,
              395
            ],
            [
              1054,
              395
            ],
            [
              1054,
              436
            ],
            [
              170,
              436
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.8183989524841309,
          "coordinate": [
            171,
            439,
            1211,
            482
          ],
          "order": 6,
          "polygon_points": [
            [
              171,
              439
            ],
            [
              1211,
              439
            ],
            [
              1211,
              482
            ],
            [
              171,
              482
            ]
          ]
        },
        {
          "cls_id": 17,
          "label": "paragraph_title",
          "score": 0.8466764092445374,
          "coordinate": [
            222,
            615,
            1470,
            653
          ],
          "order": 7,
          "polygon_points": [
            [
              222,
              615
            ],
            [
              1470,
              615
            ],
            [
              1470,
              653
            ],
            [
              222,
              653
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.9198204278945923,
          "coordinate": [
            169,
            681,
            1497,
            760
          ],
          "order": 8,
          "polygon_points": [
            [
              169,
              681
            ],
            [
              1497,
              681
            ],
            [
              1497,
              760
            ],
            [
              169,
              760
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.9104965925216675,
          "coordinate": [
            170,
            789,
            1548,
            867
          ],
          "order": 9,
          "polygon_points": [
            [
              170,
              789
            ],
            [
              1548,
              789
            ],
            [
              1548,
              867
            ],
            [
              170,
              867
            ]
          ]
        },
        {
          "cls_id": 14,
          "label": "image",
          "score": 0.9603873491287231,
          "coordinate": [
            172,
            894,
            1517,
            1497
          ],
          "order": null,
          "polygon_points": [
            [
              172,
              894
            ],
            [
              1517,
              894
            ],
            [
              1517,
              1497
            ],
            [
              172,
              1497
            ]
          ]
        },
        {
          "cls_id": 22,
          "label": "text",
          "score": 0.9272012114524841,
          "coordinate": [
            165,
            1546,
            1524,
            1700
          ],
          "order": 10,
          "polygon_points": [
            [
              165,
              1546
            ],
            [
              1524,
              1546
            ],
            [
              1524,
              1700
            ],
            [
              165,
              1700
            ]
          ]
        }
      ]
    }
  },
  "NOTES": {
    "effort": "low",
    "models_used": [
      "paddleocr-vl-1.6"
    ],
    "score": "NA",
    "needs_review": "NA",
    "candidates_output": {},
    "image_preprocessing": {
      "image_dims": "1700x2200",
      "blur_score": 1573.5798502673797,
      "comments": "Clarity: Clear (Not blurry)"
    },
    "pii_detector": {
      "pii_detected": "FALSE",
      "pii_information": "NA"
    },
    "parsing_duration_ms": 10577
  }
}

