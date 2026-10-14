"""
extract.py
-----------
Vision-based invoice/document extraction with multi-pass comparison.

Supports:
- Standard invoice fields plus customer/vendor details.
- Document-specific fields in ``additional_fields``.
- Two extraction passes, with a third pass for unresolved handwritten/mixed
  documents.
- Field-by-field merging, disagreement tracking, and conservative review flags.

Requires OPENAI_API_KEY and the ``openai`` package.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

MODEL = "gpt-4o"
_client: OpenAI | None = None

CONFIDENCE_LEVELS = {"high", "medium", "low"}

# Standard fields are merged like any other field, but these fields are
# financially critical and can force human review.
_SIMPLE_FIELDS = [
    "vendor_name",
    "customer_name",
    "customer_address",
    "vendor_address",
    "tax_registration_number",
    "payment_terms",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "subtotal",
    "tax_amount",
    "total_amount",
]
_CRITICAL_FIELDS = {"invoice_number", "total_amount", "subtotal"}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        _client = OpenAI(api_key=api_key)
    return _client


EXTRACTION_SYSTEM_PROMPT = """
You are an expert invoice and business-document data-entry clerk. You will be
shown an image of a document that may be printed, handwritten, or mixed.

Read carefully. Handwritten fields are common: take extra care with ambiguous
digits and letters (1/7, 0/6, 5/8, 3/8). Do not guess silently. If a character is
genuinely unclear, explain this in that field's notes and lower confidence.

Return ONLY valid JSON (no Markdown fences or commentary) matching this shape:

{
  "document_type": "printed" | "handwritten" | "mixed",
  "vendor_name": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "customer_name": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "customer_address": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "vendor_address": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "tax_registration_number": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "payment_terms": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "invoice_number": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "invoice_date": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "due_date": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "po_number": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "currency": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "subtotal": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "tax_amount": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "total_amount": {"value": "", "confidence": "high"|"medium"|"low", "notes": ""},
  "additional_fields": {
    "field_name_in_snake_case": {
      "value": "", "confidence": "high"|"medium"|"low", "notes": ""
    }
  },
  "line_items": [
    {"description": "", "qty": "", "unit_price": "", "total": ""}
  ],
  "overall_confidence": "high"|"medium"|"low"
}

Rules:
- Use an empty string for fields genuinely absent from the document.
- Transcribe dates exactly as written; do not reformat or infer missing date parts.
- Amounts should contain digits and a decimal point only, without currency symbols
  or thousands separators (for example "10796.50").
- Distinguish the supplier/vendor from the customer/buyer. Do not put the vendor
  name into customer_name or vice versa.
- Extract customer and vendor addresses, tax registration identifiers, and
  payment terms when present.
- Put useful document-specific fields not covered by the standard schema into
  additional_fields. Examples include delivery address/date, shipping charges,
  discount, bank details, payment method, reference number, service period,
  project/site name, contact details, and other labels that carry business data.
- Use concise snake_case keys for additional_fields and keep names consistent
  across similar documents. Do not duplicate standard fields there.
- Only extract information supported by the document. Never invent values.
- Each field object must include value, confidence, and notes.
- overall_confidence should be low if any financially critical field
  (total_amount, subtotal, invoice_number) is below high confidence.
- Be conservative about handwritten or mixed documents.
"""

USER_PROMPT_PASS_1 = (
    "Extract all invoice and business-document fields from this image using the "
    "schema exactly. Include all useful document-specific fields."
)
USER_PROMPT_PASS_2 = (
    "Independently extract all fields again using the schema exactly. Inspect "
    "handwritten digits, dates, names, totals, and reference numbers carefully."
)
USER_PROMPT_PASS_3 = (
    "This is a third independent tie-breaking read. Carefully inspect every field, "
    "especially any handwriting or fields that could have been confused. If a "
    "field is illegible, say so in notes instead of guessing."
)


@dataclass
class FieldResult:
    value: str
    confidence: str
    notes: str = ""
    agreement: bool = True


@dataclass
class ExtractionResult:
    document_type: str
    fields: dict[str, FieldResult]
    line_items: list[dict[str, str]]
    overall_confidence: str
    needs_review: bool
    disagreements: list[str] = field(default_factory=list)
    passes_used: int = 2
    raw_pass_1: dict[str, Any] = field(default_factory=dict)
    raw_pass_2: dict[str, Any] = field(default_factory=dict)
    raw_pass_3: dict[str, Any] | None = None


def _image_to_data_url(image_bytes: bytes) -> str:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def _run_single_pass(image_bytes: bytes, user_prompt: str) -> dict[str, Any]:
    response = _get_client().chat.completions.create(
        model=MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_to_data_url(image_bytes),
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
    )
    raw_text = response.choices[0].message.content
    if not raw_text:
        raise ValueError("The model returned an empty extraction response")
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError("The model response must be a JSON object")
    return data


def _normalize_for_compare(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace(",", "")


def _as_field_object(value: Any) -> dict[str, Any]:
    """Normalize a field value from a model response to a safe field object."""
    if isinstance(value, dict):
        raw_value = value.get("value", "")
        confidence = str(value.get("confidence", "low")).lower()
        if confidence not in CONFIDENCE_LEVELS:
            confidence = "low"
        notes = value.get("notes", "")
        return {
            "value": "" if raw_value is None else str(raw_value),
            "confidence": confidence,
            "notes": "" if notes is None else str(notes),
        }
    if value is None:
        return {"value": "", "confidence": "low", "notes": ""}
    # Be defensive if a model emits a scalar rather than the requested object.
    return {"value": str(value), "confidence": "low", "notes": "Model returned a scalar field"}


def _get_pass_fields(pass_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Combine standard and additional field objects into one flat mapping."""
    fields: dict[str, dict[str, Any]] = {}
    for key, value in pass_data.items():
        if key in {"additional_fields", "line_items", "document_type", "overall_confidence"}:
            continue
        if isinstance(value, dict) and "value" in value:
            fields[str(key)] = _as_field_object(value)

    additional = pass_data.get("additional_fields", {})
    if isinstance(additional, dict):
        for key, value in additional.items():
            if not isinstance(value, dict) or "value" not in value:
                continue
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            # Standard fields take precedence over duplicate dynamic fields.
            fields.setdefault(normalized_key, _as_field_object(value))
    return fields


def _needs_third_pass(pass_1: dict[str, Any], pass_2: dict[str, Any]) -> bool:
    document_type = str(pass_1.get("document_type", "printed")).lower()
    if document_type not in {"handwritten", "mixed"}:
        return False

    fields_1 = _get_pass_fields(pass_1)
    fields_2 = _get_pass_fields(pass_2)
    for key in set(fields_1) | set(fields_2):
        v1 = (fields_1.get(key) or {}).get("value", "")
        v2 = (fields_2.get(key) or {}).get("value", "")
        if _normalize_for_compare(v1) != _normalize_for_compare(v2):
            return True

    for key in _CRITICAL_FIELDS:
        if (fields_1.get(key) or {}).get("confidence", "low") != "high":
            return True
    return False


def _majority_value(values: list[str]) -> tuple[str, bool]:
    """Return the majority value and whether every pass agreed."""
    if not values:
        return "", True

    normalized = [_normalize_for_compare(value) for value in values]
    if len(set(normalized)) == 1:
        return values[0], True

    if len(values) >= 3:
        counts: dict[str, int] = {}
        for normalized_value in normalized:
            counts[normalized_value] = counts.get(normalized_value, 0) + 1
        best_normalized, best_count = max(counts.items(), key=lambda item: item[1])
        if best_count > len(values) / 2:
            for value, normalized_value in zip(values, normalized):
                if normalized_value == best_normalized:
                    return value, False

    # No majority (e.g. three different readings): don't silently choose pass 1.
    # Return an empty value so the field must be manually resolved.
    return "", False


def _merge_passes(passes: list[dict[str, Any]]) -> ExtractionResult:
    if not passes:
        raise ValueError("At least one extraction pass is required")

    disagreements: list[str] = []
    pass_fields = [_get_pass_fields(pass_data) for pass_data in passes]
    all_field_names: list[str] = []

    # Stable ordering: standard fields first, then new fields in first-seen order.
    for key in _SIMPLE_FIELDS:
        if any(key in current for current in pass_fields):
            all_field_names.append(key)
    for current in pass_fields:
        for key in current:
            if key not in all_field_names:
                all_field_names.append(key)

    fields: dict[str, FieldResult] = {}
    for key in all_field_names:
        candidates = [current.get(key, {"value": "", "confidence": "low", "notes": ""})
                      for current in pass_fields]
        values = [str(candidate.get("value", "") or "") for candidate in candidates]
        chosen_value, agree = _majority_value(values)

        if agree:
            matching_indices = [
                index for index, value in enumerate(values)
                if _normalize_for_compare(value) == _normalize_for_compare(chosen_value)
            ]
            chosen_idx = matching_indices[0] if matching_indices else 0
            confidence = candidates[chosen_idx].get("confidence", "low")
            notes = candidates[chosen_idx].get("notes", "")
        else:
            # Even when a 2-to-1 majority exists, mark the field low-confidence
            # because at least one independent read disagreed.
            confidence = "low"
            notes = "Extraction passes disagreed. Readings: " + repr(values)
            disagreements.append(key)

        fields[key] = FieldResult(
            value=chosen_value,
            confidence=confidence if confidence in CONFIDENCE_LEVELS else "low",
            notes=notes,
            agreement=agree,
        )

    # Line items remain sourced from pass 1, as in the previous implementation.
    item_counts = {
        len(pass_data.get("line_items", []) or [])
        if isinstance(pass_data.get("line_items", []), list)
        else 0
        for pass_data in passes
    }
    if len(item_counts) > 1:
        disagreements.append("line_items_count")

    raw_line_items = passes[0].get("line_items", []) or []
    line_items: list[dict[str, str]] = []
    if isinstance(raw_line_items, list):
        for item in raw_line_items:
            if isinstance(item, dict):
                line_items.append({
                    str(key): "" if value is None else str(value)
                    for key, value in item.items()
                    if key in {"description", "qty", "unit_price", "total"}
                })

    document_type = str(passes[0].get("document_type", "printed")).lower()
    if document_type not in {"printed", "handwritten", "mixed"}:
        document_type = "mixed"

    confidence_values = [
        str(pass_data.get("overall_confidence", "low")).lower()
        for pass_data in passes
    ]
    overall_confidence = (
        confidence_values[0]
        if confidence_values[0] in CONFIDENCE_LEVELS
        else "low"
    )

    if any(key in _CRITICAL_FIELDS for key in disagreements):
        overall_confidence = "low"
    if any(
        key in fields and fields[key].confidence != "high"
        for key in _CRITICAL_FIELDS
    ):
        overall_confidence = "low"

    needs_review = (
        overall_confidence != "high"
        or document_type in {"handwritten", "mixed"}
        or bool(disagreements)
        or any(
            fields[key].confidence != "high"
            for key in _CRITICAL_FIELDS
            if key in fields
        )
    )

    return ExtractionResult(
        document_type=document_type,
        fields=fields,
        line_items=line_items,
        overall_confidence=overall_confidence,
        needs_review=needs_review,
        disagreements=list(dict.fromkeys(disagreements)),
        passes_used=len(passes),
        raw_pass_1=passes[0],
        raw_pass_2=passes[1] if len(passes) > 1 else {},
        raw_pass_3=passes[2] if len(passes) > 2 else None,
    )


def extract_invoice(image_bytes: bytes) -> ExtractionResult:
    """
    Run two independent extraction passes. For handwritten/mixed documents,
    run a third pass when fields disagree or critical fields are not high
    confidence. Merge all standard and dynamic fields into one result.
    """
    pass_1 = _run_single_pass(image_bytes, USER_PROMPT_PASS_1)
    pass_2 = _run_single_pass(image_bytes, USER_PROMPT_PASS_2)
    passes = [pass_1, pass_2]

    if _needs_third_pass(pass_1, pass_2):
        passes.append(_run_single_pass(image_bytes, USER_PROMPT_PASS_3))

    return _merge_passes(passes)
