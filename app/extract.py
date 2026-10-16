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
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
_CRITICAL_FIELDS = {
    "invoice_number",
    "invoice_date",
    "total_amount",
    "subtotal",
}


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
- Use an empty string for fields genuinely absent from the document. Do not treat a blank printed field as an extraction failure or invent a value.

- Transcribe dates exactly as written. Do not infer missing date parts,
  silently correct a date, or choose a date based on what seems plausible.
- For handwritten dates, inspect each digit and the date separators carefully.
  If any digit or date component has more than one plausible reading, lower
  that field's confidence to "low" and explain the ambiguity in notes.
  Include alternative readings in notes when they can be identified from
  the image, but keep the value as the best literal reading rather than
  silently substituting an alternative.
- Do not treat agreement between extraction passes as proof that an
  ambiguous handwritten date is correct.
- For comparison only, equivalent date formats may be treated alike.


- Amounts should contain digits and a decimal point only, without currency symbols or thousands separators (for example "10796.50").
- Distinguish the supplier/vendor from the customer/buyer. Do not put the vendor name into customer_name or vice versa.
- Extract customer and vendor addresses, tax registration identifiers, contact numbers, payment terms, PO numbers, and bank details when visible.

Party and field association:
- Identify which section of the document each value belongs to before extracting it.
- Distinguish supplier/vendor details in the invoice header from customer/buyer details in the bill-to, customer, or handwritten customer section.
- Never use a supplier's printed telephone number as the customer's contact number merely because the supplier number is clearer.
- For every contact number, use its nearby label and document location to determine ownership. If ownership is ambiguous, use low confidence and explain the ambiguity in notes rather than guessing.
- If both supplier and customer contact numbers are present, preserve them separately using consistent snake_case keys such as vendor_contact_number and customer_contact_number in additional_fields. Do not silently substitute one for the other.
- Use the standard contact_number key only when the number's role is clear and the schema's standard field represents that role. When the role is supplier or customer specific, use the corresponding explicit key.
- Apply the same party-association rule to names, addresses, tax registration numbers, and bank details. Do not assume that a value belongs to the customer just because it appears near a handwritten entry.

Handwritten values and line-item completeness:
- Read handwritten text independently from nearby printed text. Do not treat a printed supplier detail as a handwritten customer detail.
- For every line item, extract its description, quantity, unit price, and amount when visible and supported by the document. Use an empty string for individual values that are genuinely absent or unreadable; explain unreadable values in notes.
- Include all visible line items. Do not invent additional items to make the extracted line-item sum match the stated subtotal.
- If the line-item sum differs from the stated subtotal, preserve the extracted values and explain the discrepancy in notes. Do not change amounts to force a match. The application should perform the numerical reconciliation and flag discrepancies for review.
- Preserve the original reading of uncertain dates, telephone numbers, TRNs, IBANs, and account numbers. If characters are unclear, state the uncertainty in notes rather than silently correcting the value.

Bank details:
- Preserve EVERY account shown. Put bank details in additional_fields using consistent snake_case keys.
- If multiple accounts are present, include all of them in a single bank_details value, clearly separating bank name, account number, IBAN, and SWIFT/BIC for each account.
- Do not silently combine details from different accounts or assign an account identifier to the wrong bank.

Additional fields:
- Put useful document-specific fields not covered by the standard schema into additional_fields. Examples include delivery address/date, shipping charges, discount, payment method, reference number, service period, project/site name, contact details, and other labels that carry business data.
- Use concise snake_case keys for additional_fields and keep names consistent across similar documents. Prefer bank_details, account_number, iban, swift_code, vendor_contact_number, and customer_contact_number when appropriate. Do not duplicate standard fields there unless needed to preserve a distinct, separately labelled value.

General extraction and confidence:
- Only extract information supported by the document. Never invent values.
- Each field object must include value, confidence, and notes.
- Set overall_confidence to low if any financially critical field (total_amount, subtotal, invoice_number) is below high confidence.
- Classify a document as handwritten when the invoice content is primarily handwritten, printed when primarily printed, and mixed only when both printed and handwritten business data are materially present.
- Be conservative about uncertain handwritten values, especially totals, dates, invoice numbers, TRNs, phone numbers, IBANs, and account numbers.
- Distinguish between a genuinely absent value and a value that is present but unreadable. Do not invent either; explain unreadable values in notes.
"""

TRN_VERIFICATION_PROMPT = """
Read the printed UAE Tax Registration Number (TRN) visible beside the TRN label.

- Transcribe the digits exactly as printed.
- Do not add, remove, correct, or normalize any digits.
- Do not infer a number from the expected TRN format.
- If any digit is unclear, report the uncertainty instead of guessing.
- Return the transcribed digits, the number of digits counted, and a confidence rating.
- Do not extract or modify any other invoice fields.

Return valid JSON with these keys:
{
  "trn": "",
  "digit_count": 0,
  "confidence": "high | medium | low",
  "notes": ""
}
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
    line_item_reconciliation: dict[str, str] = field(default_factory=dict)
    line_item_arithmetic: dict[str, str] = field(default_factory=dict)


def _image_to_data_url(image_bytes: bytes) -> str:
    """Build a data URL using the image's actual MIME type when detectable."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        mime_type = "image/jpeg"
    elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        mime_type = "image/webp"
    elif image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        mime_type = "image/gif"
    else:
        # PNG is the safest fallback for the image formats already normalized
        # by the application's preprocessing pipeline.
        mime_type = "image/png"
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


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


def _normalize_for_compare(value: Any, field_name: str = "") -> str:
    """Normalize harmless formatting differences without hiding real conflicts."""
    if value is None:
        return ""

    text = str(value).strip().casefold()
    if not text:
        return ""

    key = re.sub(r"[^a-z0-9]+", "_", field_name.casefold()).strip("_")

    # Financial amounts: 1,600 / 1600.00 / AED 1,600.00 should compare alike.
    if key in {"subtotal", "tax_amount", "total_amount", "unit_price", "total", "amount"}:
        numeric = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
        try:
            return format(Decimal(numeric).normalize(), "f")
        except (InvalidOperation, ValueError):
            pass

    # Tax identifiers, IBANs, SWIFT codes, and phone numbers commonly vary by
    # spacing or punctuation but should otherwise match exactly.

    if key in {
        "tax_registration_number",
        "trn",
        "account_number",
        "iban",
        "swift_code",
        "contact_number",
        "customer_contact_number",
        "vendor_contact_number",
        "phone",
        "phone_number",
    }:
        return re.sub(r"[^a-z0-9]", "", text)


    # Date formatting differences: 10-Oct-26, 10/10/2026, 10.10.2026.
    if key in {"invoice_date", "due_date", "date", "dated"}:
        compact = re.sub(r"[./\s]+", "-", text)
        for fmt in ("%d-%b-%y", "%d-%B-%y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"):
            try:
                parsed = datetime.strptime(compact, fmt).date()
                return parsed.isoformat()
            except ValueError:
                continue
        # Avoid relying on a parser for ambiguous dates; normalize separators.
        return re.sub(r"[^a-z0-9]", "", compact)

    # General text: ignore case, repeated whitespace, and superficial punctuation.
    text = re.sub(r"\s+", " ", text)
    if key.endswith("address") or key in {"vendor_name", "customer_name", "bank_details"}:
        text = re.sub(r"[^\w ]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text


def _validate_uae_trn(value: Any) -> tuple[bool, str]:
    """Validate the format of a UAE TRN without changing its digits."""
    digits = re.sub(r"\D", "", str(value or ""))

    if not digits:
        return False, "TRN is missing."

    if len(digits) != 15:
        return False, (
            f"UAE TRN should contain 15 digits; found {len(digits)}. "
            "Verify against the original document."
        )

    return True, ""



def _parse_decimal_amount(value: Any) -> Decimal | None:
    """Parse monetary amounts safely without guessing ambiguous formats."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # Remove supported currency labels and symbols.
    cleaned = re.sub(r"(?i)\bAED\b", "", text)
    cleaned = re.sub(r"[\s₹$€£]", "", cleaned)

    # Accept commas only as correctly grouped thousands separators.
    # Examples accepted: 1600, 1,600, 1,600.50, -250.00
    # Examples rejected: 1,23, 12,34,567, 1.234,56
    amount_pattern = (
        r"[+-]?"
        r"(?:"
        r"\d{1,3}(?:,\d{3})+|\d+"
        r")"
        r"(?:\.\d*)?"
        r"|[+-]?\.\d+"
    )

    if not re.fullmatch(amount_pattern, cleaned):
        return None

    # Remove commas only after validating their placement.
    normalized = cleaned.replace(",", "")

    try:
        amount = Decimal(normalized)
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def _validate_line_item_arithmetic(
    line_items: list[dict[str, Any]] | None,
    tolerance: Decimal = Decimal("0.01"),
) -> dict[str, str]:
    """Validate quantity × unit price against each line item's total."""
    result = {
        "status": "inconclusive",
        "notes": "",
        "mismatched_items": "",
    }

    if not line_items:
        result["notes"] = "No line items were extracted."
        return result

    mismatches: list[str] = []
    incomplete: list[str] = []

    for index, item in enumerate(line_items, start=1):
        if not isinstance(item, dict):
            incomplete.append(str(index))
            continue

        qty = _parse_decimal_amount(item.get("qty"))
        unit_price = _parse_decimal_amount(item.get("unit_price"))
        total = _parse_decimal_amount(item.get("total"))

        if qty is None or unit_price is None or total is None:
            incomplete.append(str(index))
            continue

        if abs((qty * unit_price) - total) > tolerance:
            mismatches.append(str(index))

    if mismatches:
        result["status"] = "mismatch"
        result["mismatched_items"] = ", ".join(mismatches)
        result["notes"] = (
            "Quantity multiplied by unit price differs from the line-item "
            f"total for item(s): {result['mismatched_items']}."
        )
    elif incomplete:
        result["notes"] = (
            "Quantity, unit price, or total is missing or invalid for item(s): "
            + ", ".join(incomplete)
        )
    else:
        result["status"] = "matched"
        result["notes"] = (
            "Quantity multiplied by unit price agrees with every line-item total."
        )

    return result


def _reconcile_line_items(
    line_items: list[dict[str, Any]] | None,
    subtotal: Any,
) -> dict[str, str]:
    """Compare line-item totals to subtotal with an AED 0.01 tolerance."""
    result = {
    "status": "inconclusive",
    "line_items_total": "",
    "sum": "",
    "subtotal": "",
    "difference": "",
    "notes": "",
    }

    if not line_items:
        result["notes"] = "No line items were extracted."
        return result

    parsed_subtotal = _parse_decimal_amount(subtotal)
    if parsed_subtotal is None:
        result["notes"] = "Subtotal is missing or invalid."
        return result

    item_totals: list[Decimal] = []
    for index, item in enumerate(line_items, start=1):
        if not isinstance(item, dict):
            result["notes"] = f"Line item {index} is not a valid object."
            return result

        parsed_total = _parse_decimal_amount(item.get("total"))
        if parsed_total is None:
            result["notes"] = (
                f"Line item {index} has a missing or invalid total."
            )
            return result
        item_totals.append(parsed_total)

    line_sum = sum(item_totals, Decimal("0"))
    difference = line_sum - parsed_subtotal

    def _format_decimal(value: Decimal) -> str:
        """Format decimals without unnecessary trailing zeros."""
        return format(value.normalize(), "f")


    result["line_items_total"] = _format_decimal(line_sum)
    result["sum"] = result["line_items_total"]
    result["subtotal"] = _format_decimal(parsed_subtotal)
    result["difference"] = _format_decimal(difference)


    if abs(difference) <= Decimal("0.01"):
        result["status"] = "matched"
        result["notes"] = (
            "Line-item totals agree with the subtotal within AED 0.01."
        )
    else:
        result["status"] = "mismatch"
        result["notes"] = (
            "The sum of line-item totals differs from the invoice subtotal."
        )
    return result


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



def _majority_value(values: list[str], field_name: str = "") -> tuple[str, bool]:
    """Return the selected value and whether all passes support it.

    A missing reading alongside a non-empty reading is uncertainty, not
    agreement. Normalize equivalent formats before comparing values.
    """
    if not values:
        return "", True

    normalized_values = [
        _normalize_for_compare(str(value or "").strip(), field_name)
        if str(value or "").strip()
        else ""
        for value in values
    ]

    non_empty_indices = [
        i for i, value in enumerate(normalized_values) if value
    ]

    if not non_empty_indices:
        return "", True

    # A missing reading must not be treated as confirmation.
    has_empty_reading = len(non_empty_indices) != len(values)

    effective_values = [normalized_values[i] for i in non_empty_indices]
    unique_values = set(effective_values)

    if len(unique_values) == 1:
        chosen_value = str(values[non_empty_indices[0]]).strip()
        return chosen_value, not has_empty_reading

    # A majority can select a candidate, but conflicting readings remain
    # a disagreement and must trigger review.
    if len(effective_values) >= 3:
        counts: dict[str, int] = {}
        for value in effective_values:
            counts[value] = counts.get(value, 0) + 1

        best_normalized, best_count = max(
            counts.items(), key=lambda item: item[1]
        )

        if best_count > len(effective_values) / 2:
            for index in non_empty_indices:
                if normalized_values[index] == best_normalized:
                    return str(values[index]).strip(), False

    # No safe majority: preserve the first non-empty reading for review.
    return str(values[non_empty_indices[0]]).strip(), False



def _line_items_signature(
    line_items: Any,
) -> tuple[tuple[str, ...], ...] | None:
    """Return a normalized signature for comparing extracted line items."""
    if not isinstance(line_items, list):
        return None

    signature = []

    for item in line_items:
        if not isinstance(item, dict):
            return None

        signature.append(
            (
                _normalize_for_compare(
                    item.get("description", ""), "description"
                ),
                _normalize_for_compare(item.get("qty", ""), "qty"),
                _normalize_for_compare(
                    item.get("unit_price", ""), "unit_price"
                ),
                _normalize_for_compare(item.get("total", ""), "total"),
            )
        )

    return tuple(signature)


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

    # Merge extracted fields across passes.
    fields: dict[str, FieldResult] = {}

    for key in all_field_names:
        candidates = [
            current.get(
                key,
                {"value": "", "confidence": "low", "notes": ""},
            )
            for current in pass_fields
        ]

        values = [
            str(candidate.get("value", "") or "")
            for candidate in candidates
        ]

        chosen_value, agree = _majority_value(values, key)

        if agree:
            matching_indices = [
                index
                for index, value in enumerate(values)
                if str(value or "").strip()
                and _normalize_for_compare(value, key)
                == _normalize_for_compare(chosen_value, key)
            ]

            chosen_idx = matching_indices[0] if matching_indices else 0
            confidence = candidates[chosen_idx].get("confidence", "low")
            notes = candidates[chosen_idx].get("notes", "")

        else:
            # Preserve a candidate for review rather than silently losing
            # extracted information when independent passes disagree.
            non_empty_values = [
                str(value).strip()
                for value in values
                if str(value or "").strip()
            ]

            confidence = "low"
            notes = "Extraction passes disagreed. Readings: " + repr(values)
            disagreements.append(key)

            if non_empty_values:
                # Keep the first reading as an explicitly unverified candidate.
                # The notes retain every reading for human comparison.
                chosen_value = non_empty_values[0]
            else:
                chosen_value = ""

        fields[key] = FieldResult(
            value=chosen_value,
            confidence=(
                confidence
                if confidence in CONFIDENCE_LEVELS
                else "low"
            ),
            notes=notes,
            agreement=agree,
        )

    # Validate UAE TRNs without modifying the extracted digits.
    invalid_trn_detected = False

    for trn_key in ("tax_registration_number", "trn"):
        trn_field = fields.get(trn_key)

        if trn_field and trn_field.value.strip():
            valid_trn, trn_message = _validate_uae_trn(trn_field.value)

            if not valid_trn:
                invalid_trn_detected = True
                trn_field.confidence = "low"
                trn_field.notes = "; ".join(
                    part
                    for part in (trn_field.notes, trn_message)
                    if part
                )

    # Compare line-item counts across extraction passes.
    item_counts = {
        len(pass_data.get("line_items", []) or [])
        if isinstance(pass_data.get("line_items", []), list)
        else 0
        for pass_data in passes
    }

    if len(item_counts) > 1:
        disagreements.append("line_items_count")

    # Compare line-item content, not just the number of items.
    # This catches different descriptions, quantities, unit prices, or totals.
    item_signatures = [
        _line_items_signature(pass_data.get("line_items", []))
        for pass_data in passes
    ]

    comparable_signatures = [
        signature
        for signature in item_signatures
        if signature is not None
    ]

    if (
        len(comparable_signatures) == len(item_signatures)
        and len(set(comparable_signatures)) > 1
    ):
        disagreements.append("line_items")

    # Preserve the existing policy of taking line items from pass 1.
    raw_line_items = passes[0].get("line_items", []) or []
    line_items: list[dict[str, str]] = []

    if isinstance(raw_line_items, list):
        for item in raw_line_items:
            if isinstance(item, dict):
                line_items.append(
                    {
                        str(key): "" if value is None else str(value)
                        for key, value in item.items()
                        if key in {
                            "description",
                            "qty",
                            "unit_price",
                            "total",
                        }
                    }
                )

    # Reconcile line items against the extracted subtotal.
    subtotal_field = fields.get("subtotal")
    subtotal_value = subtotal_field.value if subtotal_field else ""

    line_item_reconciliation = _reconcile_line_items(
        line_items,
        subtotal_value,
    )
    line_item_arithmetic = _validate_line_item_arithmetic(line_items)

    if line_item_arithmetic["status"] != "matched":
        disagreements.append("line_items_arithmetic")

    if line_item_reconciliation["status"] != "matched":
        disagreements.append("line_items_subtotal")

    # Normalize document type.
    document_type = str(
        passes[0].get("document_type", "printed")
    ).lower()

    if document_type not in {"printed", "handwritten", "mixed"}:
        document_type = "mixed"

    # Confidence must be conservative across every extraction pass.
    # Any low or invalid confidence signal makes the merged result low.
    # Medium is retained only when no pass reports low or invalid confidence.
    confidence_values = [
        str(pass_data.get("overall_confidence", "low")).lower()
        for pass_data in passes
    ]

    if any(
        value not in CONFIDENCE_LEVELS or value == "low"
        for value in confidence_values
    ):
        overall_confidence = "low"
    elif any(value == "medium" for value in confidence_values):
        overall_confidence = "medium"
    else:
        overall_confidence = "high"

    # Critical financial and identity fields must be reviewed when they disagree.
    critical_review_fields = _CRITICAL_FIELDS | {
        "invoice_date",
        "tax_registration_number",
        "trn",
        "tax_amount",
        "iban",
        "account_number",
        "swift_code",
        "bank_details",
        "contact_number",
        "customer_contact_number",
        "vendor_contact_number",
    }

    critical_disagreement = any(
        key in critical_review_fields
        for key in disagreements
    )

    critical_low_confidence = any(
        key in fields and fields[key].confidence != "high"
        for key in critical_review_fields
    )

    missing_critical_fields = any(
        key not in fields or not fields[key].value.strip()
        for key in (
            "invoice_number",
            "invoice_date",
            "subtotal",
            "total_amount",
        )
    )

    # Critical disagreements, low-confidence critical fields, and invalid
    # TRNs must never be overridden by a high overall-confidence signal.
    if critical_disagreement or critical_low_confidence or invalid_trn_detected:
        overall_confidence = "low"

    # Decide whether human review is required.
    needs_review = (
        overall_confidence != "high"
        or document_type in {"handwritten", "mixed"}
        or critical_disagreement
        or critical_low_confidence
        or missing_critical_fields
        or invalid_trn_detected
        or "line_items_count" in disagreements
        or "line_items" in disagreements
        or line_item_reconciliation["status"] != "matched"
        or line_item_arithmetic["status"] != "matched"
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
        line_item_reconciliation=line_item_reconciliation,
        line_item_arithmetic=line_item_arithmetic,
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
