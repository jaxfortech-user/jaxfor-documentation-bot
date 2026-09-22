"""
pipeline.py
------------
Orchestrates processing of a single file end-to-end, and the
poll-the-watched-folder loop that finds new files to process.

Full flow per file:
  download (drive, service account)
    -> preprocess (PyMuPDF + OpenCV)
    -> extract (GPT-4o, 2-3 passes)
    -> validate (confidence gate + arithmetic checks)
    -> append row to the vendor's Sheet tab (sheets.py, OAuth)
    -> move the source file to "Processed" or "NeedsReview"
       (drive.py, service account)

Auth split, worth remembering when debugging:
  - app.drive (service account): watches "J - Invoices", downloads
    files, moves files between subfolders it already has Editor
    access to. Never creates new top-level Drive objects.
  - app.sheets (OAuth, the customer's own Google identity): the only
    thing that creates the output folder/spreadsheet, since service
    accounts have zero Drive storage quota on a personal account.
"""

from __future__ import annotations

import logging
import os

from app import drive
from app.preprocess import process_document
from app.extract import extract_invoice, ExtractionResult
from app.validate import validate_extraction
from app.sheets import find_or_create_output_spreadsheet, append_extraction_row

logger = logging.getLogger("jaxfor.pipeline")

PROCESSED_FOLDER_NAME = "Processed"
NEEDS_REVIEW_FOLDER_NAME = "NeedsReview"

# Cached across ticks so we're not re-resolving folder/spreadsheet IDs
# (and re-hitting the Sheets API) on every single file.
_spreadsheet_id: str | None = None
_processed_folder_id: str | None = None
_needs_review_folder_id: str | None = None


def _extraction_result_to_row_dict(result: ExtractionResult) -> dict:
    return {
        "document_type": result.document_type,
        "overall_confidence": result.overall_confidence,
        "needs_review": result.needs_review,
        "disagreements": result.disagreements,
        "fields": {k: vars(v) for k, v in result.fields.items()},
    }


def _get_spreadsheet_id() -> str:
    global _spreadsheet_id
    if _spreadsheet_id is None:
        _, _spreadsheet_id = find_or_create_output_spreadsheet()
    return _spreadsheet_id


def _get_routing_folder_ids(watch_folder_id: str) -> tuple[str, str]:
    global _processed_folder_id, _needs_review_folder_id
    owner_email = os.environ.get("OWNER_EMAIL")

    if _processed_folder_id is None:
        _processed_folder_id = drive.find_or_create_subfolder(
            watch_folder_id, PROCESSED_FOLDER_NAME, owner_email=owner_email
        )
    if _needs_review_folder_id is None:
        _needs_review_folder_id = drive.find_or_create_subfolder(
            watch_folder_id, NEEDS_REVIEW_FOLDER_NAME, owner_email=owner_email
        )
    return _processed_folder_id, _needs_review_folder_id


def process_one_file(file: drive.DriveFile, watch_folder_id: str) -> None:
    logger.info("Processing file: %s (%s)", file.name, file.id)

    file_bytes = drive.download_file(file.id)

    pages = process_document(file_bytes, file.mime_type)
    logger.info("Preprocessed into %d page(s)", len(pages))

    # Invoices are typically single-page; multi-page handling can be
    # added later if a real multi-page case shows up.
    result = extract_invoice(pages[0].image_bytes)

    logger.info(
        "Extraction done — type=%s confidence=%s needs_review=%s "
        "disagreements=%s passes_used=%d",
        result.document_type,
        result.overall_confidence,
        result.needs_review,
        result.disagreements,
        result.passes_used,
    )

    validation = validate_extraction(result)
    if validation.needs_review:
        logger.info("Flagged for review: %s", "; ".join(validation.reasons))

    row = _extraction_result_to_row_dict(result)
    # Reflect validate.py's verdict in the row too — it can catch
    # things (arithmetic mismatches, missing PO) that extract.py's
    # own confidence gate never sees.
    row["needs_review"] = validation.needs_review
    row["disagreements"] = list(dict.fromkeys(result.disagreements + validation.reasons))

    spreadsheet_id = _get_spreadsheet_id()
    vendor_name = result.fields.get("vendor_name").value if "vendor_name" in result.fields else ""

    tab = append_extraction_row(
        spreadsheet_id,
        vendor_name,
        row,
        source_file=file.name,
        drive_file_id=file.id,
    )
    logger.info("Row appended to tab: %s", tab)

    processed_folder_id, needs_review_folder_id = _get_routing_folder_ids(watch_folder_id)
    destination_folder_id = needs_review_folder_id if validation.needs_review else processed_folder_id

    drive.move_file(file.id, watch_folder_id, destination_folder_id)
    logger.info(
        "Moved %s -> %s",
        file.name,
        NEEDS_REVIEW_FOLDER_NAME if validation.needs_review else PROCESSED_FOLDER_NAME,
    )


def poll_and_process() -> None:
    """
    Called on every scheduler tick. Lists whatever is currently
    sitting directly in the watched folder and processes each one.
    Processed files get moved OUT of the watched folder (into
    Processed/NeedsReview), so anything found here is by definition
    unhandled.
    """
    folder_id = os.environ["DRIVE_WATCH_FOLDER_ID"]

    try:
        files = drive.list_new_files(folder_id)
    except Exception:
        logger.exception("Failed to list files in watched folder")
        return

    if not files:
        logger.debug("No new files found.")
        return

    logger.info("Found %d new file(s) to process.", len(files))

    for f in files:
        try:
            process_one_file(f, folder_id)
        except Exception:
            # One bad file should never take down the polling loop —
            # log it and move on to the next file.
            logger.exception("Failed to process file %s (%s)", f.name, f.id)