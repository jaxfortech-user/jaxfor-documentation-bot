"""Quick manual test for app/sheets.py — run with: python test_sheets.py"""
import os
from dotenv import load_dotenv
load_dotenv()

from app.extract import extract_invoice
from app.sheets import find_or_create_output_spreadsheet, append_extraction_row

with open("sample_invoice.png", "rb") as f:
    image_bytes = f.read()

result = extract_invoice(image_bytes)
row = {
    "document_type": result.document_type,
    "overall_confidence": result.overall_confidence,
    "needs_review": result.needs_review,
    "disagreements": result.disagreements,
    "fields": {k: vars(v) for k, v in result.fields.items()},
}

folder_id, spreadsheet_id = find_or_create_output_spreadsheet()
print(f"Folder ID: {folder_id}")
print(f"Spreadsheet ID: {spreadsheet_id}")
print(f"Spreadsheet URL: https://docs.google.com/spreadsheets/d/{spreadsheet_id}")

vendor_name = row["fields"]["vendor_name"]["value"]
tab = append_extraction_row(
    spreadsheet_id,
    vendor_name,
    row,
    source_file="sample_invoice.png",
    drive_file_id="test-run-no-drive-id",
)
print(f"Row appended to tab: {tab}")