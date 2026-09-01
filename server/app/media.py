"""Media types: sniffed from the bytes, decided once, carried everywhere.

Three call sites used to answer "what kind of file is this?" independently —
the document store from the filename, the receipt reader from the filename
again with only a png/jpeg branch, the invoice reader from the filename with a
pdf branch somebody remembered to add. So a PDF receipt reached Vertex labelled
`image/jpeg` and came back `400 Provided image is not valid`, while the same
PDF through AP worked. One fact, three derivations, two of them wrong.

Two rules here, and the second is the one that was missing:

- **Sniff the bytes first.** `filename` is optional on a submission — an API
  caller or a drag-and-drop can send bytes with no name at all — and the
  extension is a claim by the submitter, not evidence. Magic numbers are the
  document; the extension is the fallback for the formats that have none.
- **Say plainly what a model can read.** A `.heic` off an iPhone or a `.docx`
  is a perfectly good artifact to *store* and a thing no multimodal call can
  read. Refusing it here — before the call — turns an opaque provider 400 into
  a sentence a person can act on, and stops the retry budget being spent
  earning the same refusal twice.
"""

from __future__ import annotations

PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
WEBP = "image/webp"
GIF = "image/gif"
HEIC = "image/heic"
TIFF = "image/tiff"
PLAIN = "text/plain"
UNKNOWN = "application/octet-stream"

#: What a multimodal ModelCall in this deployment can actually be handed.
#: Gemini reads PDFs natively, which is why a receipt does not need rendering
#: to an image first — the failure this module exists for was never a missing
#: capability, only a mislabelled one.
MODEL_READABLE: frozenset[str] = frozenset({PDF, PNG, JPEG, WEBP})

#: Formats a person plausibly submits that no model call can read. Named
#: rather than lumped into "unsupported" so the refusal can say what to do
#: instead — the difference between a message that helps and one that blames.
_ADVICE: dict[str, str] = {
    HEIC: "iPhone HEIC — resubmit as PDF or JPEG, or turn on "
          "Settings › Camera › Formats › Most Compatible",
    TIFF: "TIFF — resubmit as PDF or PNG",
    GIF: "GIF — resubmit as PDF, PNG or JPEG",
    PLAIN: "a text file carries no page to read — resubmit the original PDF "
           "or a photo of the receipt",
    UNKNOWN: "the file type could not be identified — resubmit as PDF, PNG "
             "or JPEG",
}

#: Magic numbers, longest prefix first so `RIFF….WEBP` is not shadowed. Only
#: the formats that actually reach a document store here; an unrecognized
#: header falls through to the extension rather than guessing.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", PDF),
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"\xff\xd8\xff", JPEG),
    (b"GIF87a", GIF),
    (b"GIF89a", GIF),
    (b"II*\x00", TIFF),
    (b"MM\x00*", TIFF),
)

_EXTENSIONS: dict[str, str] = {
    ".pdf": PDF, ".png": PNG, ".jpg": JPEG, ".jpeg": JPEG, ".webp": WEBP,
    ".gif": GIF, ".heic": HEIC, ".heif": HEIC, ".tif": TIFF, ".tiff": TIFF,
    ".txt": PLAIN, ".csv": PLAIN,
}


class UnsupportedMediaError(Exception):
    """The artifact is fine; no model in this deployment can read it.

    Carries the media type so a caller can file the refusal against the
    document rather than only against the run, and a sentence a submitter can
    act on — which is the whole difference between this and the provider's
    own `400 Provided image is not valid`.
    """

    #: Read by `runs.retryable`. Handing the same unreadable bytes to the same
    #: model a second time is not a retry, it is a wait.
    retryable = False

    def __init__(self, media_type: str, filename: str = "") -> None:
        advice = _ADVICE.get(media_type, f"{media_type} cannot be read by a model")
        named = f"'{filename}' is {advice}" if filename else advice
        super().__init__(f"{named}. Readable: PDF, PNG, JPEG, WEBP.")
        self.media_type = media_type
        self.filename = filename


def sniff(data: bytes | None, filename: str = "", declared: str = "") -> str:
    """The media type of these bytes, by evidence and then by claim.

    Order is deliberate: the header beats the extension, and the extension
    beats what the caller declared. A browser's `declared` type is the least
    reliable of the three — it comes from the same extension via the OS table,
    except when it is the empty string or `application/octet-stream`, which is
    what a drag-and-drop of an unregistered type produces.
    """
    if data:
        for signature, media_type in _SIGNATURES:
            if data.startswith(signature):
                return media_type
        # WEBP is `RIFF<4 byte length>WEBP`, so it cannot be a flat prefix.
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return WEBP
        # HEIC/HEIF is an ISO-BMFF box: `<4 byte length>ftyp<brand>`.
        if data[4:8] == b"ftyp" and data[8:12] in (
            b"heic", b"heix", b"hevc", b"mif1", b"heim", b"msf1"
        ):
            return HEIC

    suffix = _suffix(filename)
    if suffix in _EXTENSIONS:
        return _EXTENSIONS[suffix]
    if declared and declared != UNKNOWN:
        return declared
    return UNKNOWN


def is_model_readable(media_type: str) -> bool:
    return media_type in MODEL_READABLE


def require_model_readable(media_type: str, filename: str = "") -> str:
    """Return the type, or refuse with something a submitter can act on."""
    if not is_model_readable(media_type):
        raise UnsupportedMediaError(media_type, filename)
    return media_type


def page_count(data: bytes | None, media_type: str) -> int:
    """How many pages the artifact has. 1 for anything that is not a PDF.

    A cheap structural count rather than a parse: `/Type /Page` outside a
    `/Pages` node, which is right for the linear PDFs a scanner or a phone
    produces and is allowed to be approximate for the pathological ones. The
    document card claimed "1 page(s)" for every PDF before this, which is a
    confident wrong answer where an approximate one will do.
    """
    if media_type != PDF or not data:
        return 1
    count = data.count(b"/Type /Page") - data.count(b"/Type /Pages")
    if count <= 0:
        count = data.count(b"/Type/Page") - data.count(b"/Type/Pages")
    return max(count, 1)


def _suffix(filename: str) -> str:
    lowered = (filename or "").lower().strip()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot > 0 else ""
