
import unittest

from decimal import Decimal

from app.extract import (
    _merge_passes,
    _normalize_for_compare,
    _parse_decimal_amount,
    _reconcile_line_items,
    _validate_uae_trn,
    _validate_line_item_arithmetic,
)


# ---------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------

def field(value, confidence="high", notes=""):
    return {
        "value": value,
        "confidence": confidence,
        "notes": notes,
    }


def invoice_pass(
    *,
    invoice_number="INV-001",
    invoice_date="10-Oct-2026",
    subtotal="1000.00",
    tax_amount="50.00",
    total_amount="1050.00",
    trn="100315588200003",
    document_type="printed",
    overall_confidence="high",
    line_items=None,
    additional_fields=None,
    invoice_date_confidence="high",
    invoice_date_notes="",
):
    return {
        "document_type": document_type,
        "overall_confidence": overall_confidence,
        "invoice_number": field(invoice_number),
        "invoice_date": field(
            invoice_date,
            confidence=invoice_date_confidence,
            notes=invoice_date_notes,
        ),
        "subtotal": field(subtotal),
        "tax_amount": field(tax_amount),
        "total_amount": field(total_amount),
        "tax_registration_number": field(trn),
        "line_items": line_items or [],

        "additional_fields": additional_fields or {},
    }


# ---------------------------------------------------------
# UAE TRN validation
# ---------------------------------------------------------

class TestUaeTrnValidation(unittest.TestCase):

    def test_valid_15_digit_trn(self):
        self.assertEqual(
            _validate_uae_trn("100315588200003"),
            (True, ""),
        )

    def test_trn_with_separators(self):
        valid, _ = _validate_uae_trn("100 315 588 200 003")
        self.assertTrue(valid)

    def test_rejects_14_digits(self):
        valid, message = _validate_uae_trn("10031558820003")
        self.assertFalse(valid)
        self.assertIn("15 digits", message)

    def test_rejects_16_digits_without_changing_value(self):
        original = "1003155882000003"
        valid, message = _validate_uae_trn(original)

        self.assertFalse(valid)
        self.assertIn("16", message)
        self.assertEqual(original, "1003155882000003")

    def test_rejects_missing_trn(self):
        valid, message = _validate_uae_trn("")

        self.assertFalse(valid)
        self.assertIn("missing", message.lower())

    def test_rejects_letters_only(self):
        valid, _ = _validate_uae_trn("ABCDEFGHIJKLMNO")
        self.assertFalse(valid)


# ---------------------------------------------------------
# Field normalization
# ---------------------------------------------------------

class TestFieldNormalization(unittest.TestCase):

    def test_amount_formatting_is_equivalent(self):
        self.assertEqual(
            _normalize_for_compare("AED 1,600.00", "subtotal"),
            _normalize_for_compare("1600", "subtotal"),
        )

    def test_date_formatting_is_equivalent(self):
        self.assertEqual(
            _normalize_for_compare("10-Oct-2026", "invoice_date"),
            _normalize_for_compare("10/10/2026", "invoice_date"),
        )

    def test_trn_digit_difference_is_not_hidden(self):
        self.assertNotEqual(
            _normalize_for_compare(
                "100315588200003",
                "tax_registration_number",
            ),
            _normalize_for_compare(
                "1003155882000003",
                "tax_registration_number",
            ),
        )

    def test_account_digit_difference_is_not_hidden(self):
        self.assertNotEqual(
            _normalize_for_compare(
                "11857235820001",
                "account_number",
            ),
            _normalize_for_compare(
                "118572358200001",
                "account_number",
            ),
        )


    def test_customer_contact_number_formatting_is_equivalent(self):
        self.assertEqual(
            _normalize_for_compare(
                "+971 50 123 4567",
                "customer_contact_number",
            ),
            _normalize_for_compare(
                "+971501234567",
                "customer_contact_number",
            ),
        )

    def test_vendor_contact_number_formatting_is_equivalent(self):
        self.assertEqual(
            _normalize_for_compare(
                "+971 4 123 4567",
                "vendor_contact_number",
            ),
            _normalize_for_compare(
                "+97141234567",
                "vendor_contact_number",
            ),
        )


# ---------------------------------------------------------
# Line-item reconciliation
# ---------------------------------------------------------

class TestLineItemReconciliation(unittest.TestCase):

    def test_matching_line_items_equal_subtotal(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "200",
            },
            {
                "description": "Item B",
                "qty": "4",
                "unit_price": "50",
                "total": "200",
            },
        ]

        result = _reconcile_line_items(items, "400")

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["line_items_total"], "400")
        self.assertEqual(result["difference"], "0")

    def test_mismatch_is_detected(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "200",
            },
            {
                "description": "Item B",
                "qty": "4",
                "unit_price": "50",
                "total": "250",
            },
        ]

        result = _reconcile_line_items(items, "400")

        self.assertEqual(result["status"], "mismatch")
        self.assertNotEqual(result["difference"], "0")

    def test_missing_line_total_is_inconclusive(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "",
            },
        ]

        result = _reconcile_line_items(items, "200")

        self.assertEqual(result["status"], "inconclusive")

    def test_invoice_31445_crossed_out_amounts_do_not_reconcile_to_subtotal(self):
        # Only the visible 350 and 250 amounts are supplied as line totals.
        # The handwritten 1600 subtotal must not cause an invented line item.
        items = [
            {
                "description": "Intelligent sediment / carbon / UF / RO / filters",
                "qty": "1",
                "unit_price": "350",
                "total": "350",
            },
            {
                "description": "Small carbon / jumbo PP",
                "qty": "1",
                "unit_price": "250",
                "total": "250",
            },
        ]
        original_items = [dict(item) for item in items]

        result = _reconcile_line_items(items, "1600")

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["line_items_total"], "600")
        self.assertEqual(result["difference"], "-1000")
        self.assertEqual(items, original_items)
        self.assertEqual(len(items), 2)

    def test_incomplete_line_item_amount_is_inconclusive_not_fabricated(self):
        items = [
            {"description": "Filter items", "qty": "1", "unit_price": "350", "total": "350"},
            {"description": "AMC", "qty": "1", "unit_price": "", "total": ""},
        ]
        original_items = [dict(item) for item in items]

        result = _reconcile_line_items(items, "1600")

        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(items, original_items)
        self.assertEqual(items[1]["total"], "")

    def test_empty_line_items_are_inconclusive(self):
        result = _reconcile_line_items([], "1000")

        self.assertEqual(result["status"], "inconclusive")

    def test_one_cent_rounding_tolerance(self):
        items = [
            {
                "description": "Item A",
                "qty": "1",
                "unit_price": "100",
                "total": "100.01",
            },
        ]

        result = _reconcile_line_items(items, "100")

        self.assertEqual(result["status"], "matched")

    def test_difference_over_one_cent_is_mismatch(self):
        items = [
            {
                "description": "Item A",
                "qty": "1",
                "unit_price": "100",
                "total": "100.02",
            },
        ]

        result = _reconcile_line_items(items, "100")

        self.assertEqual(result["status"], "mismatch")

    def test_invalid_line_total_is_inconclusive(self):
        items = [
            {
                "description": "Item A",
                "qty": "1",
                "unit_price": "100",
                "total": "unknown",
            },
        ]

        result = _reconcile_line_items(items, "100")

        self.assertEqual(result["status"], "inconclusive")

    def test_missing_subtotal_is_inconclusive(self):
        items = [
            {
                "description": "Item A",
                "qty": "1",
                "unit_price": "100",
                "total": "100",
            },
        ]

        result = _reconcile_line_items(items, "")

        self.assertEqual(result["status"], "inconclusive")

    def test_missing_quantity_and_unit_price_are_not_fabricated(self):
        items = [
            {
                "description": "Item A",
                "qty": "",
                "unit_price": "",
                "total": "100",
            }
        ]

        result = _reconcile_line_items(items, "100")

        # Current reconciliation checks line totals against subtotal only.
        self.assertEqual(result["status"], "matched")
        self.assertEqual(items[0]["qty"], "")
        self.assertEqual(items[0]["unit_price"], "")


    def test_quantity_unit_price_mismatch_currently_needs_separate_validation(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "150",
            }
        ]

        result = _reconcile_line_items(items, "150")

        # Documents current behavior; this does not validate qty × unit_price.
        self.assertEqual(result["status"], "matched")


    def test_malformed_line_item_is_inconclusive(self):
        result = _reconcile_line_items(
            ["invalid item"],
            "100",
        )

        self.assertEqual(result["status"], "inconclusive")

    def test_invalid_trn_lowers_overall_confidence_even_when_all_passes_agree(self):
        invalid_trn = "1003155882000003"

        passes = [
            invoice_pass(
                trn=invalid_trn,
                overall_confidence="high",
            )
            for _ in range(3)
        ]

        result = _merge_passes(passes)

        trn = result.fields["tax_registration_number"]

        self.assertEqual(trn.value, invalid_trn)
        self.assertEqual(trn.confidence, "low")
        self.assertIn("15 digits", trn.notes)
        self.assertEqual(result.overall_confidence, "low")
        self.assertTrue(result.needs_review)


    def test_nan_line_total_is_inconclusive(self):
        result = _reconcile_line_items(
            [{"description": "Item A", "total": "NaN"}],
            "100",
        )
        self.assertEqual(result["status"], "inconclusive")

    def test_infinity_subtotal_is_inconclusive(self):
        result = _reconcile_line_items(
            [{"description": "Item A", "total": "100"}],
            "Infinity",
        )
        self.assertEqual(result["status"], "inconclusive")

    def test_non_dictionary_line_item_is_inconclusive(self):
        result = _reconcile_line_items(
            ["invalid item"],
            "100",
        )
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("not a valid object", result["notes"])

    def test_currency_prefixed_amount_is_parsed_safely(self):
        result = _reconcile_line_items(
            [{"description": "Item A", "total": "AED 100.00"}],
            "100.00",
        )
        self.assertEqual(result["status"], "matched")



# ---------------------------------------------------------
# Merge and manual-review safety
# ---------------------------------------------------------

class TestMergeReviewSafety(unittest.TestCase):

    def test_malformed_trn_requires_review(self):
        result = _merge_passes([
            invoice_pass(trn="1003155882000003"),
            invoice_pass(trn="1003155882000003"),
        ])

        self.assertEqual(
            result.fields["tax_registration_number"].value,
            "1003155882000003",
        )
        self.assertEqual(
            result.fields["tax_registration_number"].confidence,
            "low",
        )
        self.assertTrue(result.needs_review)

    def test_valid_trn_is_not_flagged_by_format_validator(self):
        result = _merge_passes([
            invoice_pass(),
            invoice_pass(),
        ])

        trn_field = result.fields["tax_registration_number"]

        self.assertEqual(trn_field.value, "100315588200003")
        self.assertEqual(trn_field.confidence, "high")

    def test_conflicting_invoice_numbers_require_review(self):
        result = _merge_passes([
            invoice_pass(invoice_number="INV-001"),
            invoice_pass(invoice_number="INV-002"),
        ])

        self.assertTrue(result.needs_review)
        self.assertIn("invoice_number", result.disagreements)

    def test_conflicting_bank_details_require_review(self):
        pass1 = invoice_pass(
            additional_fields={
                "bank_details": field(
                    "IBAN AE090260001015897630301; "
                    "SWIFT EBILAEADXXX"
                )
            }
        )

        pass2 = invoice_pass(
            additional_fields={
                "bank_details": field(
                    "IBAN AE090260001015897630302; "
                    "SWIFT EBILAEADXXX"
                )
            }
        )

        result = _merge_passes([pass1, pass2])

        self.assertTrue(result.needs_review)
        self.assertIn("bank_details", result.disagreements)


    def test_conflicting_customer_contact_requires_review(self):
        pass1 = invoice_pass(
            additional_fields={
                "customer_contact_number": field("0501234567"),
            }
        )
        pass2 = invoice_pass(
            additional_fields={
                "customer_contact_number": field("0509876543"),
            }
        )

        result = _merge_passes([pass1, pass2])

        self.assertTrue(result.needs_review)
        self.assertIn(
            "customer_contact_number",
            result.disagreements,
        )

    def test_conflicting_vendor_contact_requires_review(self):
        pass1 = invoice_pass(
            additional_fields={
                "vendor_contact_number": field("+971501234567"),
            }
        )
        pass2 = invoice_pass(
            additional_fields={
                "vendor_contact_number": field("+971509876543"),
            }
        )

        result = _merge_passes([pass1, pass2])

        self.assertTrue(result.needs_review)
        self.assertIn(
            "vendor_contact_number",
            result.disagreements,
        )


    def test_generic_and_customer_contact_keys_are_not_silently_merged(self):
        pass1 = invoice_pass(
            additional_fields={
                "contact_number": field("0501234567"),
            }
        )
        pass2 = invoice_pass(
            additional_fields={
                "customer_contact_number": field("0509876543"),
            }
        )

        result = _merge_passes([pass1, pass2])

        # Until ownership is established, keep both readings distinct.
        self.assertIn("contact_number", result.fields)
        self.assertIn("customer_contact_number", result.fields)


    def test_customer_and_vendor_contacts_remain_separate_when_both_present(self):
        customer_number = "0506579987"
        vendor_number = "+97148866112"
        passes = [
            invoice_pass(
                additional_fields={
                    "customer_contact_number": field(customer_number),
                    "vendor_contact_number": field(vendor_number),
                }
            )
            for _ in range(2)
        ]

        result = _merge_passes(passes)

        self.assertIn("customer_contact_number", result.fields)
        self.assertIn("vendor_contact_number", result.fields)
        self.assertEqual(
            result.fields["customer_contact_number"].value,
            customer_number,
        )
        self.assertEqual(
            result.fields["vendor_contact_number"].value,
            vendor_number,
        )

    def test_handwritten_invoice_requires_review(self):
        result = _merge_passes([
            invoice_pass(document_type="handwritten"),
            invoice_pass(document_type="handwritten"),
        ])

        self.assertTrue(result.needs_review)

    def test_mixed_invoice_requires_review(self):
        result = _merge_passes([
            invoice_pass(document_type="mixed"),
            invoice_pass(document_type="mixed"),
        ])

        self.assertTrue(result.needs_review)

    def test_no_passes_raises_value_error(self):
        with self.assertRaises(ValueError):
            _merge_passes([])

    def test_missing_invoice_number_requires_review(self):
        result = _merge_passes([
            invoice_pass(invoice_number=""),
            invoice_pass(invoice_number=""),
        ])

        self.assertTrue(result.needs_review)

    def test_missing_invoice_date_requires_review(self):
        result = _merge_passes([
            invoice_pass(invoice_date=""),
            invoice_pass(invoice_date=""),
        ])

        self.assertTrue(result.needs_review)

    def test_uncertain_agreed_handwritten_date_requires_review(self):
        passes = [
            invoice_pass(
                document_type="mixed",
                invoice_date="09/01/2023",
                invoice_date_confidence="low",
                invoice_date_notes="Handwritten day/month is ambiguous.",
            )
            for _ in range(3)
        ]

        result = _merge_passes(passes)

        self.assertEqual(result.fields["invoice_date"].value, "09/01/2023")
        self.assertEqual(result.fields["invoice_date"].confidence, "low")
        self.assertTrue(result.needs_review)
        self.assertEqual(result.overall_confidence, "low")

    def test_missing_subtotal_requires_review(self):
        result = _merge_passes([
            invoice_pass(subtotal=""),
            invoice_pass(subtotal=""),
        ])

        self.assertTrue(result.needs_review)

    def test_missing_total_requires_review(self):
        result = _merge_passes([
            invoice_pass(total_amount=""),
            invoice_pass(total_amount=""),
        ])

        self.assertTrue(result.needs_review)

    def test_line_item_mismatch_requires_review(self):
        items = [
            {
                "description": "Item A",
                "qty": "1",
                "unit_price": "100",
                "total": "100",
            },
        ]

        result = _merge_passes([
            invoice_pass(subtotal="200", line_items=items),
            invoice_pass(subtotal="200", line_items=items),
        ])

        self.assertTrue(result.needs_review)
        self.assertIn(
            "line_items_subtotal",
            result.disagreements,
        )
        self.assertEqual(
            result.line_item_reconciliation["status"],
            "mismatch",
        )


    def test_missing_reading_does_not_hide_field_uncertainty(self):
        pass1 = invoice_pass(invoice_date="09/01/2023")
        pass2 = invoice_pass(invoice_date="")
        result = _merge_passes([pass1, pass2])

        self.assertTrue(result.needs_review)
        self.assertEqual(
            result.fields["invoice_date"].confidence,
            "low",
        )


    def test_line_item_arithmetic_mismatch_requires_review(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "150",
            },
        ]

        result = _merge_passes([
            invoice_pass(
                subtotal="150",
                tax_amount="0",
                total_amount="150",
                line_items=items,
            ),
            invoice_pass(
                subtotal="150",
                tax_amount="0",
                total_amount="150",
                line_items=items,
            ),
        ])

        self.assertTrue(result.needs_review)
        self.assertIn("line_items_arithmetic", result.disagreements)
        self.assertEqual(
            result.line_item_arithmetic["status"],
            "mismatch",
        )

    def test_matching_line_item_arithmetic_is_validated(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "200",
            },
        ]

        result = _merge_passes([
            invoice_pass(
                subtotal="200",
                tax_amount="0",
                total_amount="200",
                line_items=items,
            ),
            invoice_pass(
                subtotal="200",
                tax_amount="0",
                total_amount="200",
                line_items=items,
            ),
        ])

        self.assertEqual(
            result.line_item_arithmetic["status"],
            "matched",
        )
        self.assertNotIn(
            "line_items_arithmetic",
            result.disagreements,
        )



    def test_line_item_present_only_in_second_pass_is_not_silently_ignored(self):
        pass1 = invoice_pass(
            subtotal="300",
            line_items=[
                {
                    "description": "Item A",
                    "qty": "1",
                    "unit_price": "100",
                    "total": "100",
                }
            ],
        )
        pass2 = invoice_pass(
            subtotal="300",
            line_items=[
                {
                    "description": "Item A",
                    "qty": "1",
                    "unit_price": "100",
                    "total": "100",
                },
                {
                    "description": "Item B",
                    "qty": "1",
                    "unit_price": "200",
                    "total": "200",
                },
            ],
        )

        result = _merge_passes([pass1, pass2])

        self.assertTrue(result.needs_review)
        self.assertIn(
            "line_items_subtotal",
            result.disagreements,
        )


    def test_matching_line_items_do_not_trigger_reconciliation_review(self):
        items = [
            {
                "description": "Item A",
                "qty": "2",
                "unit_price": "100",
                "total": "200",
            },
            {
                "description": "Item B",
                "qty": "4",
                "unit_price": "50",
                "total": "200",
            },
        ]

        result = _merge_passes([
            invoice_pass(
                subtotal="400",
                tax_amount="20",
                total_amount="420",
                line_items=items,
            ),
            invoice_pass(
                subtotal="400",
                tax_amount="20",
                total_amount="420",
                line_items=items,
            ),
        ])

        self.assertEqual(
            result.line_item_reconciliation["status"],
            "matched",
        )
    def test_line_item_count_disagreement_requires_review_even_if_subtotal_matches(self):
        pass1 = invoice_pass(
            subtotal="300",
            line_items=[
                {
                    "description": "Item A",
                    "qty": "1",
                    "unit_price": "100",
                    "total": "100",
                },
                {
                    "description": "Item B",
                    "qty": "1",
                    "unit_price": "200",
                    "total": "200",
                },
            ],
        )
        pass2 = invoice_pass(
            subtotal="300",
            line_items=[
                {
                    "description": "Item A",
                    "qty": "1",
                    "unit_price": "100",
                    "total": "100",
                }
            ],
        )

        result = _merge_passes([pass1, pass2])

        self.assertIn("line_items_count", result.disagreements)
        self.assertTrue(result.needs_review)


    def test_conflicting_overall_confidence_uses_conservative_result(self):
        passes = [
            invoice_pass(overall_confidence="high"),
            invoice_pass(overall_confidence="low"),
        ]

        result = _merge_passes(passes)

        self.assertEqual(result.overall_confidence, "low")
        self.assertTrue(result.needs_review)

    def test_same_line_item_count_but_different_amounts_requires_review(self):
        pass1 = invoice_pass(
            subtotal="300",
            line_items=[
                {"description": "Item A", "qty": "1",
                 "unit_price": "100", "total": "100"},
                {"description": "Item B", "qty": "1",
                 "unit_price": "200", "total": "200"},
            ],
        )
        pass2 = invoice_pass(
            subtotal="300",
            line_items=[
                {"description": "Item A", "qty": "1",
                 "unit_price": "150", "total": "150"},
                {"description": "Item B", "qty": "1",
                 "unit_price": "150", "total": "150"},
            ],
        )

        result = _merge_passes([pass1, pass2])

        self.assertTrue(result.needs_review)
        self.assertIn("line_items", result.disagreements)

    def test_noncritical_disagreement_remains_visible(self):
        pass1 = invoice_pass(
            additional_fields={
                "delivery_address": field("Warehouse A")
            }
        )
        pass2 = invoice_pass(
            additional_fields={
                "delivery_address": field("Warehouse B")
            }
        )

        result = _merge_passes([pass1, pass2])

        self.assertIn("delivery_address", result.disagreements)
        self.assertFalse(result.fields["delivery_address"].agreement)
        self.assertEqual(result.fields["delivery_address"].confidence, "low")

    def test_reordered_identical_line_items_are_flagged_by_current_ordered_comparison(self):
        item_a = {
            "description": "Item A",
            "qty": "1",
            "unit_price": "100",
            "total": "100",
        }
        item_b = {
            "description": "Item B",
            "qty": "1",
            "unit_price": "200",
            "total": "200",
        }

        pass1 = invoice_pass(
            subtotal="300",
            line_items=[item_a, item_b],
        )
        pass2 = invoice_pass(
            subtotal="300",
            line_items=[item_b, item_a],
        )

        result = _merge_passes([pass1, pass2])

        # Documents current behavior: ordering differences trigger review.
        self.assertIn("line_items", result.disagreements)
        self.assertTrue(result.needs_review)

class TestLineItemArithmetic(unittest.TestCase):

    def test_matching_quantity_and_price(self):
        result = _validate_line_item_arithmetic([
            {"qty": "2", "unit_price": "100", "total": "200"},
        ])
        self.assertEqual(result["status"], "matched")

    def test_quantity_price_mismatch(self):
        result = _validate_line_item_arithmetic([
            {"qty": "2", "unit_price": "100", "total": "150"},
        ])
        self.assertEqual(result["status"], "mismatch")
        self.assertIn("1", result["mismatched_items"])

    def test_missing_quantity_is_inconclusive(self):
        result = _validate_line_item_arithmetic([
            {"qty": "", "unit_price": "100", "total": "100"},
        ])
        self.assertEqual(result["status"], "inconclusive")

    def test_invalid_quantity_is_inconclusive(self):
        result = _validate_line_item_arithmetic([
            {"qty": "unknown", "unit_price": "100", "total": "100"},
        ])
        self.assertEqual(result["status"], "inconclusive")

    def test_empty_items_are_inconclusive(self):
        result = _validate_line_item_arithmetic([])
        self.assertEqual(result["status"], "inconclusive")

class TestMonetaryParsingEdgeCases(unittest.TestCase):

    def test_standard_aed_amount(self):
        self.assertEqual(
            _parse_decimal_amount("AED 1,600.00"),
            Decimal("1600.00"),
        )

    def test_malformed_comma_grouping_is_rejected(self):
        self.assertIsNone(_parse_decimal_amount("1,23"))

    def test_decimal_comma_format_is_rejected(self):
        self.assertIsNone(_parse_decimal_amount("1.234,56"))

    def test_nan_is_rejected(self):
        self.assertIsNone(_parse_decimal_amount("NaN"))

    def test_infinity_is_rejected(self):
        self.assertIsNone(_parse_decimal_amount("Infinity"))

    def test_multiple_decimal_points_are_rejected(self):
        self.assertIsNone(_parse_decimal_amount("1.2.3"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
