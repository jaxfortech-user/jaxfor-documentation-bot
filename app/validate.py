"""
validate.py
-------------
Confidence gate + internal consistency checks. Decides whether an
extraction result is trustworthy enough to auto-post, or should be
routed to human review, and gives the specific reasons either way.

Checks performed:
  1. Confidence gate — trusts app.extract's own `needs_review` flag
     (which already accounts for per-field confidence, handwriting,
     and multi-pass disagreements).
  2. Arithmetic cross-check — subtotal + tax_amount should equal
     total_amount (within a small tolerance for rounding).
  3. Line-item cross-check — sum of line item totals should
     approximately equal subtotal, when line items were extracted.
  4. PO presence check — flags a missing PO number. This is
     INTERNAL-ONLY for now: there is no connected PO/ERP source to
     cross-check the number against yet. `check_po_number()` is a
     narrow, swappable hook — plug in a real lookup there later
     (e.g. against PRO Platform or a PO tracking sheet) without
     touching the rest of this module.

A NeedsReview verdict from ANY check routes the invoice to the
"NeedsReview" Drive folder instead of "Processed" — see pipeline.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.extract import ExtractionResult

AMOUNT_TOLERANCE = 0.05  # currency units; absorbs rounding noise, not real errors


@dataclass
class ValidationResult:
    needs_review: bool
    reasons: list[str] = field(default_factory=list)


def _parse_amount(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _check_arithmetic(result: ExtractionResult) -> list[str]:
    reasons: list[str] = []

    subtotal = _parse_amount(result.fields.get("subtotal", None).value if "subtotal" in result.fields else "")
    tax = _parse_amount(result.fields.get("tax_amount", None).value if "tax_amount" in result.fields else "")
    total = _parse_amount(result.fields.get("total_amount", None).value if "total_amount" in result.fields else "")

    if subtotal is not None and tax is not None and total is not None:
        expected_total = subtotal + tax
        if abs(expected_total - total) > AMOUNT_TOLERANCE:
            reasons.append(
                f"Arithmetic mismatch: subtotal ({subtotal}) + tax ({tax}) = "
                f"{expected_total:.2f}, but total_amount reads {total}"
            )

    return reasons


def _check_line_items_sum_to_subtotal(result: ExtractionResult) -> list[str]:
    reasons: list[str] = []

    subtotal = _parse_amount(result.fields.get("subtotal", None).value if "subtotal" in result.fields else "")
    if subtotal is None or not result.line_items:
        return reasons

    line_total = 0.0
    parsed_all = True
    for item in result.line_items:
        amount = _parse_amount(item.get("total", ""))
        if amount is None:
            parsed_all = False
            break
        line_total += amount

    if parsed_all and abs(line_total - subtotal) > AMOUNT_TOLERANCE:
        reasons.append(
            f"Line items sum to {line_total:.2f} but subtotal reads {subtotal} "
            f"(possible missed or misread line item)"
        )

    return reasons


def check_po_number(po_number: str) -> str | None:
    """
    Placeholder hook for cross-checking the extracted PO number
    against a real source of truth (an ERP, a PO tracking sheet,
    etc.). No such source is connected yet, so this only checks that
    a PO number was extracted at all — it never claims to have
    verified the PO is real or matches an order.

    Returns a reason string if something's worth flagging, else None.
    Swap this implementation for a real lookup once a PO source
    exists; nothing else in this module needs to change.
    """
    if not po_number or not po_number.strip():
        return "No PO number was extracted from the document"
    return None


def validate_extraction(result: ExtractionResult) -> ValidationResult:
    """
    Combines app.extract's own confidence-based needs_review verdict
    with independent arithmetic/consistency checks. ANY failing check
    forces needs_review=True, even if extract.py itself was confident —
    a document can read every field "clearly" and still not add up.
    """
    reasons: list[str] = []

    if result.needs_review:
        if result.disagreements:
            reasons.append(f"Extraction disagreements on: {', '.join(result.disagreements)}")
        if result.overall_confidence != "high":
            reasons.append(f"Overall extraction confidence: {result.overall_confidence}")
        if result.document_type == "handwritten":
            reasons.append("Document is handwritten")
        if not reasons:
            reasons.append("Flagged by extraction confidence gate")

    reasons.extend(_check_arithmetic(result))
    reasons.extend(_check_line_items_sum_to_subtotal(result))

    po_field = result.fields.get("po_number")
    po_reason = check_po_number(po_field.value if po_field else "")
    if po_reason:
        reasons.append(po_reason)

    return ValidationResult(needs_review=len(reasons) > 0, reasons=reasons)