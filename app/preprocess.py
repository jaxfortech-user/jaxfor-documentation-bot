"""
preprocess.py
--------------
Converts an incoming document (PDF or image) into a clean, upright
PNG image ready for GPT-4o vision extraction.

Pipeline:
  1. If PDF -> render page(s) to image via PyMuPDF (no poppler needed)
  2. Deskew (auto-detect rotation angle, correct it)
  3. Contrast/brightness normalization (CLAHE)
  4. Return processed image bytes (PNG) + metadata

No system dependencies beyond the pip packages below -- safe for
Railway's buildpack-style deploys (no Dockerfile required).

Dependencies: pymupdf, opencv-python-headless, numpy
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import pymupdf as fitz
import numpy as np


@dataclass
class ProcessedPage:
    index: int
    image_bytes: bytes          # PNG bytes, ready to send to GPT-4o
    width: int
    height: int
    skew_angle_deg: float       # angle that was corrected (0.0 if none)
    original_format: str        # "pdf" or "image"


def _bytes_to_cv2_image(data: bytes) -> np.ndarray:
    """Decode raw image bytes (PNG/JPEG/WEBP) into an OpenCV BGR array."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image bytes — unsupported or corrupt format")
    return img


def _pdf_to_images(pdf_bytes: bytes, dpi: int = 300) -> list[np.ndarray]:
    """
    Render every page of a PDF to an OpenCV BGR image using PyMuPDF.
    300 DPI is a good default for invoice text/handwriting legibility
    without producing huge files.
    """
    images: list[np.ndarray] = []
    zoom = dpi / 72.0  # PDF base is 72 DPI
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img_bytes = pix.tobytes("png")
            images.append(_bytes_to_cv2_image(img_bytes))

    return images


def _detect_skew_angle(gray: np.ndarray) -> float:
    """
    Estimate the skew angle of a document image using minAreaRect
    over thresholded text/content pixels. Returns degrees; positive
    = counter-clockwise correction needed.
    """
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 50:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]

    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Ignore implausibly large angles -- more likely a false detection
    # than genuine skew on a phone scan.
    if abs(angle) > 15:
        return 0.0

    return round(float(angle), 2)


def _rotate_image(img: np.ndarray, angle: float) -> np.ndarray:
    if angle == 0.0:
        return img
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _enhance_contrast(img: np.ndarray) -> np.ndarray:
    """
    CLAHE on the luminance channel to boost faint pen strokes /
    handwriting without blowing out already-clear printed text.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)
    merged = cv2.merge((l_enhanced, a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _process_single_image(img: np.ndarray, page_index: int, original_format: str) -> ProcessedPage:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    skew_angle = _detect_skew_angle(gray)
    img = _rotate_image(img, skew_angle)
    img = _enhance_contrast(img)

    success, buf = cv2.imencode(".png", img)
    if not success:
        raise RuntimeError("Failed to encode processed image to PNG")

    h, w = img.shape[:2]
    return ProcessedPage(
        index=page_index,
        image_bytes=buf.tobytes(),
        width=w,
        height=h,
        skew_angle_deg=skew_angle,
        original_format=original_format,
    )


# Image mime types cv2.imdecode can handle out of the box. webp is
# included — OpenCV's imdecode supports it via its built-in libwebp
# bindings, so no extra dependency is needed, just widening this gate.
# (Files uploaded with a misleading extension, e.g. a .jpg that is
# actually webp-encoded, are also caught here since we sniff by
# mime_type as reported by Drive, not by filename.)
_SUPPORTED_IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
)


def process_document(file_bytes: bytes, mime_type: str) -> list[ProcessedPage]:
    """
    Main entry point. Accepts raw file bytes + mime type
    (e.g. "application/pdf", "image/png", "image/jpeg", "image/webp")
    and returns one ProcessedPage per page (PDFs may have multiple
    pages; images always return exactly one).
    """
    if mime_type == "application/pdf":
        raw_images = _pdf_to_images(file_bytes)
        original_format = "pdf"
    elif mime_type in _SUPPORTED_IMAGE_MIME_TYPES:
        raw_images = [_bytes_to_cv2_image(file_bytes)]
        original_format = "image"
    else:
        raise ValueError(f"Unsupported mime type: {mime_type}")

    return [
        _process_single_image(img, i, original_format)
        for i, img in enumerate(raw_images)
    ]