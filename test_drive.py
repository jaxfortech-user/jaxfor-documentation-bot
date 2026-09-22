"""Quick manual test for app/drive.py — run with: python test_drive.py"""
import os
from dotenv import load_dotenv
load_dotenv()

from app.drive import list_new_files, download_file

folder_id = os.environ.get("DRIVE_WATCH_FOLDER_ID", "1DAckfdE-l2C7c0dEJAT2KHycraqolUVb")

files = list_new_files(folder_id)
print(f"Found {len(files)} file(s) in 'J - Invoices':\n")
for f in files:
    print(f"  - {f.name}  (id={f.id}, mime={f.mime_type})")

# Sanity-check download on the first file, if any exist
if files:
    first = files[0]
    data = download_file(first.id)
    print(f"\nDownloaded '{first.name}': {len(data)} bytes")