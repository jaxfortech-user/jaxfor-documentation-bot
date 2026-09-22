"""test_pipeline_full.py — run with: python test_pipeline_full.py"""
from dotenv import load_dotenv
load_dotenv()

from app.preprocess import process_document
from app.extract import extract_invoice
from app.validate import validate_extraction

with open("sample_invoice_handwritten.png", "rb") as f:
    file_bytes = f.read()

pages = process_document(file_bytes, "image/png")
print(f"Preprocessed {len(pages)} page(s), skew corrected: {pages[0].skew_angle_deg} deg")

result = extract_invoice(pages[0].image_bytes)
print(f"passes_used: {result.passes_used}")
print(f"document_type: {result.document_type}")
print(f"overall_confidence: {result.overall_confidence}")
print(f"disagreements: {result.disagreements}")
for k, v in result.fields.items():
    print(f"  {k}: {v.value!r} ({v.confidence}) {v.notes}")

validation = validate_extraction(result)
print(f"\nvalidate.py verdict — needs_review: {validation.needs_review}")
for r in validation.reasons:
    print(f"  - {r}")