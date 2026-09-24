"""The ingress layer: what is allowed in, checked before anything else looks at a document.

Four things are checked here — how big an upload is, what it says it is, whether it is what it says
it is, and whether reading it would cost more than it is worth — plus the rate limit in front of the
whole boundary, which lives next door because it is a token bucket rather than a document check.

Two properties matter more than the checks themselves:

* **Cheap before expensive, and nothing is decoded that has already been refused.** A document is
  refused on its size before a parser sees it, and the read that fed the check stopped one byte past
  the ceiling (`read_bounded`), so a hostile upload costs a bounded amount of memory even when it is
  ten gigabytes long.
* **A hostile document is refused, not handled.** Everything a decoder raises is caught: the bytes
  are attacker-controlled, so a reader that fails, recurses or runs out of memory is an answer
  ("not a readable PDF") rather than a defect that reaches the caller as a 500.

The checks are ordered data, not prose: `_STEPS` says which checks a declared content type is asked,
in the order they run, and the allow-list in `IngressLimits` narrows that set rather than adding to
it. A content type the boundary has no checks for cannot be let through by configuring it — the
allow-list can only take away.

Refusals stop at the first failing check for a document, so the verdicts of a refused document end
with the check that refused it, preceded by the ones that passed. Nothing here decides what a
refusal means on the wire; `claim_triage.api.surface` maps a check onto a status and an error code.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Final
from zipfile import ZipFile

from pypdf import PdfReader

from claim_triage.guards.verdict import Check, DocumentVerdicts, Verdict

if TYPE_CHECKING:
    from typing import BinaryIO

PDF: Final = "application/pdf"
"""The document class a claim arrives as in v0.1: a PDF, digital text layer or scan."""

ZIP: Final = "application/zip"
"""A bundle of documents. Accepted so that what a bundle costs to expand can be checked."""

ENCRYPTED_FLAG: Final = 0x1
"""The general purpose bit a zip entry sets to say it is encrypted (APPNOTE 4.4.4)."""


@dataclass(frozen=True, slots=True)
class Upload:
    """One document as it arrived: what the caller called it, and the bytes read so far.

    The bytes are what `read_bounded` was willing to read, not necessarily the whole upload: one
    byte past the ceiling is enough for the size check to refuse, and nothing else is asked of a
    document already over it.
    """

    filename: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class IngressLimits:
    """The ingress thresholds, as data: what this boundary takes, and how much of it.

    Configuration fills this in (`claim_triage.config.GuardSettings`), so changing a ceiling is a
    deployment change rather than a code change.
    """

    media_types: frozenset[str]
    max_submission_bytes: int
    max_bytes: int
    max_pages: int
    max_expansion_ratio: float


type Step = Callable[[Upload, IngressLimits], tuple[Verdict, ...]]
"""One check, or several that read the document once: what it finds, in the order it found it."""


def read_bounded(stream: BinaryIO, max_bytes: int) -> bytes:
    """Read at most `max_bytes + 1` bytes: enough to be certain a document is over the ceiling.

    The boundary reads request bodies through this, so a document larger than the ceiling is never
    held in memory whole. The one extra byte is what tells "at the ceiling" from "past it".
    """
    return stream.read(max_bytes + 1)


def screen(uploads: Sequence[Upload], limits: IngressLimits) -> list[DocumentVerdicts]:
    """Answer every check that applies to every upload, up to the first refusal of each."""
    return [_screened(upload, limits) for upload in uploads]


def declared_size(declared_bytes: int | None, limits: IngressLimits) -> Verdict:
    """The verdict on how much one whole submission says it carries, before anything reads it.

    A document is refused for its size after it has been read (up to the ceiling, and no further);
    a submission is refused for its size *before* the body is touched, when the transport declares
    one. That is the difference between a boundary that never buffers a hostile upload and one that
    spools it to disk and refuses it afterwards, so this runs from the middleware rather than from
    the route, where the submission would already have been parsed.

    `None` is a submission whose length the transport did not declare — a chunked request — and it
    passes here because it cannot be judged here. What bounds it then is the per-document ceiling as
    each document is read, which is stated in `docs/adr/0007` rather than left to be discovered.
    """
    if declared_bytes is None:
        return _passed(
            Check.SIZE, "the submission declares no length, so its size is bounded as it is read"
        )
    if declared_bytes > limits.max_submission_bytes:
        return _refused(
            Check.SIZE,
            f"the submission declares {declared_bytes} bytes, past the"
            f" {limits.max_submission_bytes}-byte ceiling",
        )
    return _passed(
        Check.SIZE,
        f"the submission declares {declared_bytes} bytes, within the"
        f" {limits.max_submission_bytes}-byte ceiling",
    )


def first_refusal(verdicts: Sequence[DocumentVerdicts]) -> tuple[str, Verdict] | None:
    """The first document refused and the check that refused it, or `None` if every one passed."""
    for document in verdicts:
        for verdict in document.verdicts:
            if not verdict.passed:
                return document.filename, verdict
    return None


def _screened(upload: Upload, limits: IngressLimits) -> DocumentVerdicts:
    """One document's verdicts: the checks for its declared type, stopping where one refuses."""
    verdicts: list[Verdict] = []
    for step in _STEPS.get(upload.media_type, ()):
        found = step(upload, limits)
        verdicts.extend(found)
        if not all(verdict.passed for verdict in found):
            break
    if not verdicts:
        # A content type this boundary has no checks for. The allow-list cannot admit one, so this
        # is what a configuration naming a type no ticket has implemented looks like: a refusal.
        verdicts.append(
            _refused(
                Check.MEDIA_TYPE,
                f"{upload.media_type or 'no content type'} is not a document type this boundary"
                f" takes ({_taken(limits)})",
            )
        )
    return DocumentVerdicts(
        filename=upload.filename, media_type=upload.media_type, verdicts=verdicts
    )


def _size(upload: Upload, limits: IngressLimits) -> tuple[Verdict, ...]:
    size = len(upload.content)
    if size > limits.max_bytes:
        return (
            _refused(
                Check.SIZE,
                f"{size} bytes exceeds the {limits.max_bytes}-byte ceiling",
            ),
        )
    return (_passed(Check.SIZE, f"{size} bytes is within the {limits.max_bytes}-byte ceiling"),)


def _media_type(upload: Upload, limits: IngressLimits) -> tuple[Verdict, ...]:
    if upload.media_type in limits.media_types:
        return (_passed(Check.MEDIA_TYPE, f"{upload.media_type} is a document type taken here"),)
    return (
        _refused(
            Check.MEDIA_TYPE,
            f"{upload.media_type or 'no content type'} is not one of the document types taken here"
            f" ({_taken(limits)})",
        ),
    )


def _pdf(upload: Upload, limits: IngressLimits) -> tuple[Verdict, ...]:
    """Structure, encryption and page count, from one read of the document.

    One step rather than three checks, because a PDF is parsed once: encryption is asked before the
    pages, since a reader that has not been given the password raises rather than counting them.
    """
    try:
        reader = PdfReader(BytesIO(upload.content))
    except Exception as unreadable:
        # Deliberately everything: these bytes are the attacker's, and every failure a decoder can
        # produce is the same answer — this is not a document we will read.
        return (_refused(Check.STRUCTURE, f"it is not a readable PDF: {unreadable}"),)

    structure = _passed(Check.STRUCTURE, "it is a readable PDF")
    if reader.is_encrypted:
        return (structure, _refused(Check.ENCRYPTION, "it is encrypted"))

    try:
        pages = len(reader.pages)
    except Exception as unreadable:  # a page tree is as attacker-controlled as the header
        return (structure, _refused(Check.STRUCTURE, f"its pages are unreadable: {unreadable}"))

    if pages < 1:
        # A page tree that resolves to nothing is a corrupt document, not an empty one: a claim
        # document that holds no pages is one nothing downstream can read a field out of.
        return (
            structure,
            _passed(Check.ENCRYPTION, "it is not encrypted"),
            _refused(Check.STRUCTURE, "it holds no pages"),
        )

    if pages > limits.max_pages:
        return (
            structure,
            _passed(Check.ENCRYPTION, "it is not encrypted"),
            _refused(
                Check.PAGE_COUNT, f"{pages} pages exceeds the {limits.max_pages}-page ceiling"
            ),
        )
    return (
        structure,
        _passed(Check.ENCRYPTION, "it is not encrypted"),
        _passed(Check.PAGE_COUNT, f"{pages} pages is within the {limits.max_pages}-page ceiling"),
    )


def _archive(upload: Upload, limits: IngressLimits) -> tuple[Verdict, ...]:
    """Structure, encryption and expansion, from one read of the archive's own directory.

    Nothing is decompressed to answer these: an entry's declared size and the archive's own size are
    both in the central directory, which is exactly the claim a bomb makes and the reason the
    expansion is checked before anything downstream opens it.
    """
    try:
        with ZipFile(BytesIO(upload.content)) as archive:
            entries = archive.infolist()
    except Exception as unreadable:
        return (_refused(Check.STRUCTURE, f"it is not a readable archive: {unreadable}"),)

    structure = _passed(Check.STRUCTURE, "it is a readable archive")
    encrypted = [entry.filename for entry in entries if entry.flag_bits & ENCRYPTED_FLAG]
    if encrypted:
        return (
            structure,
            _refused(Check.ENCRYPTION, f"it holds encrypted entries, starting with {encrypted[0]}"),
        )

    expanded = sum(entry.file_size for entry in entries)
    ratio = expanded / max(len(upload.content), 1)
    if ratio > limits.max_expansion_ratio:
        return (
            structure,
            _passed(Check.ENCRYPTION, "no entry is encrypted"),
            _refused(
                Check.ARCHIVE,
                f"it declares {expanded} bytes of {len(upload.content)}, {ratio:.0f}x its own"
                f" size, past the {limits.max_expansion_ratio:.0f}x ceiling",
            ),
        )
    return (
        structure,
        _passed(Check.ENCRYPTION, "no entry is encrypted"),
        _passed(
            Check.ARCHIVE,
            f"it declares {expanded} bytes of {len(upload.content)}, within the"
            f" {limits.max_expansion_ratio:.0f}x ceiling",
        ),
    )


_STEPS: Final[dict[str, tuple[Step, ...]]] = {
    PDF: (_size, _media_type, _pdf),
    ZIP: (_size, _media_type, _archive),
}
"""Which checks each document type is asked, in order: cheapest and most certain first."""


def _taken(limits: IngressLimits) -> str:
    return ", ".join(sorted(limits.media_types)) or "nothing"


def _passed(check: Check, detail: str) -> Verdict:
    return Verdict(check=check, passed=True, detail=detail)


def _refused(check: Check, detail: str) -> Verdict:
    return Verdict(check=check, passed=False, detail=detail)
