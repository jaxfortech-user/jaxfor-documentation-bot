import unittest
from unittest.mock import MagicMock, patch

from app.sheets import (
    RED_FILL,
    YELLOW_FILL,
    _build_review_format_requests,
    _field_review_color,
    append_extraction_row,
)


class TestFieldReviewColor(unittest.TestCase):
    def test_low_confidence_is_red(self):
        self.assertEqual(
            _field_review_color({"confidence": "low"}),
            RED_FILL,
        )

    def test_disagreement_is_red(self):
        self.assertEqual(
            _field_review_color(
                {"confidence": "high", "agreement": False}
            ),
            RED_FILL,
        )

    def test_medium_confidence_is_yellow(self):
        self.assertEqual(
            _field_review_color({"confidence": "medium"}),
            YELLOW_FILL,
        )

    def test_notes_are_yellow(self):
        self.assertEqual(
            _field_review_color(
                {"confidence": "high", "notes": "Handwriting unclear"}
            ),
            YELLOW_FILL,
        )

    def test_high_confidence_without_notes_has_no_highlight(self):
        self.assertIsNone(
            _field_review_color(
                {"confidence": "high", "notes": "", "agreement": True}
            )
        )


class TestReviewFormatRequests(unittest.TestCase):
    def test_arithmetic_mismatch_highlights_amount_cells(self):
        headers = [
            "Invoice Number",
            "Subtotal",
            "Tax Amount",
            "Total Amount",
        ]

        requests = _build_review_format_requests(
            sheet_id=123,
            row_index=4,
            headers=headers,
            fields={},
            disagreements=[],
            validation_reasons=[
                "Arithmetic mismatch: subtotal (100.0) + tax (5.0) = 105.00, "
                "but total_amount reads 110.0"
            ],
        )

        columns = [
            request["repeatCell"]["range"]["startColumnIndex"]
            for request in requests
        ]

        self.assertEqual(columns, [1, 2, 3])
        self.assertTrue(
            all(
                request["repeatCell"]["range"]["startRowIndex"] == 4
                for request in requests
            )
        )

    def test_missing_po_highlights_only_po_cell(self):
        headers = ["Invoice Number", "PO Number", "Total Amount"]

        requests = _build_review_format_requests(
            sheet_id=123,
            row_index=7,
            headers=headers,
            fields={},
            disagreements=[],
            validation_reasons=[
                "No PO number was extracted from the document"
            ],
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0]["repeatCell"]["range"]["startColumnIndex"],
            1,
        )

    def test_dynamic_field_header_is_supported(self):
        headers = ["Invoice Number", "Delivery Note"]
        fields = {
            "delivery_note": {
                "value": "Deliver to warehouse",
                "confidence": "low",
                "notes": "",
                "agreement": True,
            }
        }

        requests = _build_review_format_requests(
            sheet_id=123,
            row_index=2,
            headers=headers,
            fields=fields,
            disagreements=[],
            validation_reasons=[],
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0]["repeatCell"]["range"]["startColumnIndex"],
            1,
        )


class TestAppendExtractionRowFormatting(unittest.TestCase):
    @patch("app.sheets.get_sheets_service")
    @patch("app.sheets._ensure_field_headers")
    @patch("app.sheets.ensure_vendor_tab")
    def test_low_confidence_field_triggers_formatting(
        self,
        mock_ensure_vendor_tab,
        mock_ensure_headers,
        mock_get_service,
    ):
        mock_ensure_vendor_tab.return_value = "Vendor A"
        mock_ensure_headers.return_value = [
            "Invoice Number",
            "Needs Review",
        ]

        service = MagicMock()
        mock_get_service.return_value = service
        spreadsheet_api = service.spreadsheets.return_value

        spreadsheet_api.values.return_value.append.return_value.execute.return_value = {
            "updates": {
                "updatedRange": "'Vendor A'!A2:B2",
            }
        }

        spreadsheet_api.get.return_value.execute.return_value = {
            "sheets": [
                {
                    "properties": {
                        "title": "Vendor A",
                        "sheetId": 42,
                    }
                }
            ]
        }

        row = {
            "document_type": "invoice",
            "overall_confidence": "medium",
            "needs_review": True,
            "disagreements": [],
            "validation_reasons": [],
            "fields": {
                "invoice_number": {
                    "value": "INV-123",
                    "confidence": "low",
                    "notes": "",
                    "agreement": True,
                }
            },
        }

        result = append_extraction_row(
            spreadsheet_id="test-spreadsheet",
            vendor_name="Vendor A",
            row=row,
            source_file="invoice.pdf",
            drive_file_id="test-file-id",
        )

        self.assertEqual(result, "Vendor A")
        spreadsheet_api.batchUpdate.assert_called_once()

        requests = spreadsheet_api.batchUpdate.call_args.kwargs[
            "body"
        ]["requests"]

        self.assertEqual(len(requests), 1)

        cell_range = requests[0]["repeatCell"]["range"]
        self.assertEqual(cell_range["sheetId"], 42)
        self.assertEqual(cell_range["startRowIndex"], 1)
        self.assertEqual(cell_range["startColumnIndex"], 0)

        color = requests[0]["repeatCell"]["cell"][
            "userEnteredFormat"
        ]["backgroundColor"]

        self.assertEqual(color, RED_FILL)


class TestEnsureColumnCapacity(unittest.TestCase):
    @patch("app.sheets.get_sheets_service")
    def test_expands_grid_when_27th_column_is_required(self, mock_get_service):
        service = mock_get_service.return_value
        service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [
                {
                    "properties": {
                        "sheetId": 42,
                        "title": "Vendor A",
                        "gridProperties": {"columnCount": 26},
                    }
                }
            ]
        }

        from app.sheets import _ensure_column_capacity

        _ensure_column_capacity("test-spreadsheet", "Vendor A", 27)

        service.spreadsheets.return_value.batchUpdate.assert_called_once_with(
            spreadsheetId="test-spreadsheet",
            body={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": 42,
                                "gridProperties": {"columnCount": 27},
                            },
                            "fields": "gridProperties.columnCount",
                        }
                    }
                ]
            },
        )

    @patch("app.sheets.get_sheets_service")
    def test_does_not_shrink_or_expand_sufficient_grid(self, mock_get_service):
        service = mock_get_service.return_value
        service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [
                {
                    "properties": {
                        "sheetId": 42,
                        "title": "Vendor A",
                        "gridProperties": {"columnCount": 30},
                    }
                }
            ]
        }

        from app.sheets import _ensure_column_capacity

        _ensure_column_capacity("test-spreadsheet", "Vendor A", 27)

        service.spreadsheets.return_value.batchUpdate.assert_not_called()

    @patch("app.sheets.get_sheets_service")
    def test_raises_when_worksheet_is_missing(self, mock_get_service):
        service = mock_get_service.return_value
        service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": []
        }

        from app.sheets import _ensure_column_capacity

        with self.assertRaisesRegex(ValueError, "Worksheet not found"):
            _ensure_column_capacity("test-spreadsheet", "Missing Vendor", 27)

        service.spreadsheets.return_value.batchUpdate.assert_not_called()


class TestEnsureFieldHeadersCapacity(unittest.TestCase):
    @patch("app.sheets.get_sheets_service")
    @patch("app.sheets._ensure_column_capacity")
    @patch("app.sheets._read_headers")
    def test_expands_capacity_before_writing_new_headers(
        self,
        mock_read_headers,
        mock_ensure_capacity,
        mock_get_service,
    ):
        from app.sheets import _ensure_field_headers

        mock_read_headers.return_value = ["Invoice Number"]
        service = mock_get_service.return_value
        events = []

        def record_capacity(*args, **kwargs):
            events.append("capacity")

        def record_header_write(**kwargs):
            events.append("header_write")
            return MagicMock(
                execute=MagicMock(return_value={})
            )

        mock_ensure_capacity.side_effect = record_capacity
        service.spreadsheets.return_value.values.return_value.update.side_effect = (
            record_header_write
        )

        result = _ensure_field_headers(
            spreadsheet_id="test-spreadsheet",
            tab_name="Vendor A",
            fields={"vendor_name": {}},
        )

        self.assertEqual(
            result,
            ["Invoice Number", "Vendor Name"],
        )
        self.assertEqual(events, ["capacity", "header_write"])
        mock_ensure_capacity.assert_called_once_with(
            spreadsheet_id="test-spreadsheet",
            tab_name="Vendor A",
            required_columns=2,
        )


if __name__ == "__main__":
    unittest.main()
