"""Quick manual test for app/extract.py — run with: python test_extract.py"""
import json
from dotenv import load_dotenv
load_dotenv()  # picks up OPENAI_API_KEY from .env

from app.extract import extract_invoice

# Point this at a real sample invoice image (PNG/JPG).
# If you only have a PDF, run it through app.preprocess first —
# tell me its function name and I'll add that step here.
with open("sample_invoice.png", "rb") as f:
    image_bytes = f.read()

result = extract_invoice(image_bytes)

print(json.dumps({
    "document_type": result.document_type,
    "overall_confidence": result.overall_confidence,
    "needs_review": result.needs_review,
    "disagreements": result.disagreements,
    "fields": {k: vars(v) for k, v in result.fields.items()},
    "line_items": result.line_items,
}, indent=2))