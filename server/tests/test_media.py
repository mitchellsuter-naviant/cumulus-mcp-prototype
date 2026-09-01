"""What a file is, decided from the bytes rather than from its name.

The bug these are written against: a PDF receipt reached Vertex labelled
`image/jpeg` because the reader re-derived the type from the filename with a
two-branch guess, and earned `400 Provided image is not valid` twice.
"""

import base64

import pytest

from app import media
from app.documents import DocumentService, InMemoryDocumentStore
from app.runs import retryable

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Pages /Count 2 >>\n" + b"/Type /Page " * 2
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
HEIC_BYTES = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32


def test_the_header_beats_the_extension():
    """A `.jpg` holding a PDF is a PDF. The extension is a claim, not evidence."""
    assert media.sniff(PDF_BYTES, "receipt.jpg") == media.PDF


def test_a_file_with_no_name_is_still_identified():
    """The case the old guesser could not survive: `filename` is optional on a
    submission, and everything unnamed became `image/jpeg`."""
    assert media.sniff(PNG_BYTES, "") == media.PNG
    assert media.sniff(JPEG_BYTES, "") == media.JPEG
    assert media.sniff(PDF_BYTES, "") == media.PDF


def test_the_extension_answers_when_the_bytes_cannot():
    assert media.sniff(b"", "scan.webp") == media.WEBP
    assert media.sniff(None, "notes.txt") == media.PLAIN


def test_an_unidentifiable_file_says_so_rather_than_guessing():
    assert media.sniff(b"\x01\x02\x03\x04", "mystery") == media.UNKNOWN


def test_a_pdf_is_readable_by_the_model_and_a_phone_photo_is_not():
    assert media.is_model_readable(media.PDF)
    with pytest.raises(media.UnsupportedMediaError) as caught:
        media.require_model_readable(media.sniff(HEIC_BYTES, "IMG_9.HEIC"), "IMG_9.HEIC")
    # The refusal has to tell the submitter what to do instead. "Provided image
    # is not valid" is what we are replacing, and it says nothing.
    assert "resubmit as PDF or JPEG" in str(caught.value)


def test_an_unreadable_format_is_never_retried():
    """Retry-then-escalate is for a flaky network. Handing the same unreadable
    bytes to the same model twice is not a retry, it is a wait."""
    assert retryable(media.UnsupportedMediaError(media.HEIC, "a.heic")) is False


def test_a_provider_4xx_is_permanent_and_a_429_is_not():
    class ClientError(Exception):
        def __init__(self, code):
            self.code = code

    assert retryable(ClientError(400)) is False
    assert retryable(ClientError(429)) is True
    assert retryable(RuntimeError("connection reset")) is True


def test_the_store_records_what_it_sniffed_and_counts_the_pages():
    """`pages` was hardcoded to 1, so every multi-page PDF's document card
    stated a confident wrong answer."""
    service = DocumentService(InMemoryDocumentStore(), max_bytes=1_000_000)
    record = service.put_bytes(
        base64.b64encode(PDF_BYTES).decode(), filename="INVOICE_FOOD2.pdf",
        kind="receipt",
    )
    assert record.media_type == media.PDF
    assert record.pages == 2
