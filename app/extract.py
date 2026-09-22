"""
extract.py
-----------
Sends a preprocessed invoice image to GPT-4o (vision) and returns
structured, validated field data.

Design:
  1. Run extraction TWICE (always — per project decision, since
     handwritten invoices are in scope and a single pass can look
     confident while being wrong).
  2. Compare the two passes field-by-field.
  3. ADVANCED EXTRACTION for handwriting: if the document looks
     handwritten/mixed AND at least one field is still unresolved
     after 2 passes (disagreement, or a critical field below "high"
     confidence), automatically run a 3RD pass and re-merge all
     three by majority vote per field, instead of just giving up to
     "low confidence". Two agreeing reads beat one outlier; only a
     true 3-way split gets forced to "low" + flagged.
  4. Any field still unresolved after this is downgraded to "low"
     confidence and flagged for human review, regardless of what any
     individual pass reported.
  5. Return one merged result with a top-level `needs_review` flag.

Dependencies: openai
Requires: OPENAI_API_KEY environment variable
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


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        _client = OpenAI(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are an expert invoice data-entry clerk. You will be shown an image \
of an invoice, which may be cleanly printed, handwritten, or a mix of both.

Read the document carefully. Handwritten fields are common — take extra care with \
ambiguous digits and letters (e.g. 1 vs 7, 0 vs 6, 5 vs 8). Do not guess silently: \
if a character is genuinely unclear, say so in "notes" for that field rather than \
picking one option and reporting high confidence.

Return ONLY valid JSON (no markdown fences, no commentary outside the JSON) matching \
exactly this shape:

{
  "document_type": "printed" | "handwritten" | "mixed",
  "vendor_name": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "invoice_number": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "invoice_date": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "due_date": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "po_number": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "currency": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "subtotal": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "tax_amount": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "total_amount": {"value": string, "confidence": "high"|"medium"|"low", "notes": string},
  "line_items": [
    {"description": string, "qty": string, "unit_price": string, "total": string}
  ],
  "overall_confidence": "high"|"medium"|"low"
}

Rules:
- Use empty string "" for any field that is genuinely absent from the document.
- Dates: transcribe exactly as written on the document (do not reformat).
- Amounts: digits only plus a decimal point, no currency symbols or thousands separators \
(e.g. "10796.50", not "AED 10,796.50").
- "overall_confidence" should be "low" if ANY financially critical field \
(total_amount, subtotal, invoice_number) is below "high".
- If the document is handwritten or mixed, be more conservative with confidence overall.
"""

USER_PROMPT_PASS_1 = "Extract the invoice fields from this image, following the schema exactly."
USER_PROMPT_PASS_2 = (
    "Extract the invoice fields from this image, following the schema exactly. "
    "Look closely a second time at any handwritten numbers or dates before answering — "
    "double check digits that are easy to confuse (1/7, 0/6, 3/8, 5/6)."
)
USER_PROMPT_PASS_3 = (
    "Extract the invoice fields from this image, following the schema exactly. "
    "This is a tie-breaking THIRD read: two earlier reads disagreed on some fields, "
    "or handwriting made this document risky to read confidently. Take your time on "
    "every handwritten character, especially digits (1/7, 0/6, 3/8, 5/6, 2/z) and any "
    "ambiguous letters. If a field is genuinely illegible, say so in its notes rather "
    "than guessing."
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FieldResult:
    value: str
    confidence: str
    notes: str = ""
    agreement: bool = True  # did all passes used agree on this value?


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


# ---------------------------------------------------------------------------
# Core calls
# ---------------------------------------------------------------------------

def _image_to_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def _run_single_pass(image_bytes: bytes, user_prompt: str) -> dict[str, Any]:
    client = _get_client()
    data_url = _image_to_data_url(image_bytes)

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
    )

    raw_text = response.choices[0].message.content
    return json.loads(raw_text)


# ---------------------------------------------------------------------------
# Comparison / merge logic
# ---------------------------------------------------------------------------

_SIMPLE_FIELDS = [
    "vendor_name", "invoice_number", "invoice_date", "due_date",
    "po_number", "currency", "subtotal", "tax_amount", "total_amount",
]

_CRITICAL_FIELDS = {"invoice_number", "total_amount", "subtotal"}


def _normalize_for_compare(value: str) -> str:
    return (value or "").strip().lower().replace(",", "")


def _needs_third_pass(pass_1: dict[str, Any], pass_2: dict[str, Any]) -> bool:
    """
    Decides whether a document is worth spending a 3rd GPT-4o call on.
    Only triggers for handwritten/mixed docs where 2 passes weren't
    enough to agree on every field — printed docs practically never
    need this, so we don't spend the extra call/latency on them.
    """
    document_type = pass_1.get("document_type", "printed")
    if document_type not in ("handwritten", "mixed"):
        return False

    for key in _SIMPLE_FIELDS:
        v1 = (pass_1.get(key, {}) or {}).get("value", "")
        v2 = (pass_2.get(key, {}) or {}).get("value", "")
        if _normalize_for_compare(v1) != _normalize_for_compare(v2):
            return True

    # Even with full agreement, a critical field reported at less than
    # "high" confidence on a handwritten doc is worth a tie-break read —
    # both passes could be confidently wrong the same way.
    for key in _CRITICAL_FIELDS:
        if (pass_1.get(key, {}) or {}).get("confidence") != "high":
            return True

    return False


def _majority_value(values: list[str]) -> tuple[str, bool]:
    """
    Given 2 or 3 candidate values for one field (one per pass),
    returns (chosen_value, all_agreed). With 3 values: a 2-1 split
    returns the majority value with all_agreed=False (still a real
    disagreement worth flagging, just resolved rather than blind);
    a 3-way split falls back to the first pass's value.
    """
    if len(values) == 1:
        return values[0], True

    normalized = [_normalize_for_compare(v) for v in values]

    if len(set(normalized)) == 1:
        return values[0], True

    if len(values) == 3:
        counts: dict[str, int] = {}
        for v, n in zip(values, normalized):
            counts[n] = counts.get(n, 0) + 1
        best_norm, best_count = max(counts.items(), key=lambda kv: kv[1])
        if best_count >= 2:
            for v, n in zip(values, normalized):
                if n == best_norm:
                    return v, False

    return values[0], False


def _merge_passes(passes: list[dict[str, Any]]) -> ExtractionResult:
    fields: dict[str, FieldResult] = {}
    disagreements: list[str] = []

    pass_1 = passes[0]

    for key in _SIMPLE_FIELDS:
        candidates = [(p.get(key, {}) or {}) for p in passes]
        values = [c.get("value", "") for c in candidates]

        chosen_value, agree = _majority_value(values)

        # Confidence/notes come from whichever pass actually produced
        # the chosen value, so we don't attach pass 1's notes to a
        # value pass 2/3 supplied.
        chosen_idx = next(
            i for i, v in enumerate(values) if _normalize_for_compare(v) == _normalize_for_compare(chosen_value)
        )
        confidence = candidates[chosen_idx].get("confidence", "low")
        notes = candidates[chosen_idx].get("notes", "")

        if not agree:
            confidence = "low"
            disagreements.append(key)
            other_reads = [
                v for i, v in enumerate(values) if i != chosen_idx
            ]
            notes = (notes + f" | Other read(s): {other_reads}").strip(" |")

        fields[key] = FieldResult(value=chosen_value, confidence=confidence, notes=notes, agreement=agree)

    # Line items: take pass 1's list; flag for review if item count
    # differs across passes (a strong signal something was missed or
    # misread on at least one pass).
    item_counts = {len(p.get("line_items", []) or []) for p in passes}
    if len(item_counts) > 1:
        disagreements.append("line_items_count")
    line_items = pass_1.get("line_items", []) or []

    document_type = pass_1.get("document_type", "printed")
    overall_confidence = pass_1.get("overall_confidence", "low")

    # Force low overall confidence if any critical field disagreed
    if any(d in _CRITICAL_FIELDS for d in disagreements):
        overall_confidence = "low"

    needs_review = (
        overall_confidence != "high"
        or document_type == "handwritten"
        or len(disagreements) > 0
        or any(f.confidence != "high" for k, f in fields.items() if k in _CRITICAL_FIELDS)
    )

    return ExtractionResult(
        document_type=document_type,
        fields=fields,
        line_items=line_items,
        overall_confidence=overall_confidence,
        needs_review=needs_review,
        disagreements=disagreements,
        passes_used=len(passes),
        raw_pass_1=passes[0],
        raw_pass_2=passes[1],
        raw_pass_3=passes[2] if len(passes) > 2 else None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_invoice(image_bytes: bytes) -> ExtractionResult:
    """
    Runs 2 independent GPT-4o extraction passes over the given image
    and merges them into a single validated result. For a
    handwritten/mixed document where those 2 passes didn't fully
    agree, automatically runs a 3rd tie-breaking pass and re-merges
    all three by majority vote — this is the "advanced extraction"
    path for handwriting, spending the extra API call only where it's
    likely to actually change the outcome.
    """
    pass_1 = _run_single_pass(image_bytes, USER_PROMPT_PASS_1)
    pass_2 = _run_single_pass(image_bytes, USER_PROMPT_PASS_2)

    passes = [pass_1, pass_2]

    if _needs_third_pass(pass_1, pass_2):
        pass_3 = _run_single_pass(image_bytes, USER_PROMPT_PASS_3)
        passes.append(pass_3)

    return _merge_passes(passes)