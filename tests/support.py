"""Test doubles, at the boundaries the specification names, and what a test serves an app with.

The surrounding systems are one of those boundaries: the real implementation is the Postgres tables
`core-sim` owns, which the contract job exercises through the running service. The triager's tables
are the other: the real implementation is the Postgres store the pipeline records through, held to
the same promise by `tests/test_audit_transaction.py`, which needs the database the unit job has
none of. Both doubles keep what they hold in dictionaries and answer in exactly the shapes their
ports promise — nothing more, so a test cannot pass on behaviour the database would not have. Both
fingerprint writes through the same functions the stores do, so the two cannot disagree about what
counts as the same request, or about what an entry hashes to.

The rest is what more than one test module needs to drive a served stack: `free_port` and
`wait_until_listening` for serving one at all, `Skeleton` for the whole path from the entry point to
the audit entry, and the documents a guard test uploads. Those documents are built rather than
committed, because a test that asserts a page ceiling should build a PDF with the pages it means —
and because the synthetic corpus those tests will eventually use is ticket 13's work.
"""

from __future__ import annotations

import json
import re
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Final, cast
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx2
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pypdf import PdfWriter

from claim_triage.config import GuardSettings
from claim_triage.contract.models import (
    Claim,
    ClaimDocument,
    ClaimDocumentSubmission,
    ClaimParty,
    ClaimPartySubmission,
    ClaimStatus,
    ClaimStatusTransition,
    ClaimSubmission,
    Policy,
)
from claim_triage.core_sim.seed import SEED_POLICIES
from claim_triage.core_sim.store import (
    ClaimNotFound,
    IdempotencyKeyReuse,
    PolicyNotFound,
    request_fingerprint,
)
from claim_triage.guards.ingress import Upload
from claim_triage.triage import audit
from claim_triage.triage.audit import AuditEntry, AuditEntryContent
from claim_triage.triage.run import RunResult, TriageRun
from claim_triage.triage.store import TriageStoreUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from opentelemetry.sdk.trace import ReadableSpan

    from claim_triage.telemetry import Telemetry

STARTUP_DEADLINE_SECONDS: float = 10.0
"""How long a served app may take to accept connections before a test fails."""

LISTEN_POLL_SECONDS: float = 0.01
"""How long to wait between connection attempts: polled for, never assumed after a fixed sleep."""

Remembered = dict[str, object]
"""One recorded write: the fingerprint of its request, and the response it answered with."""


class InMemoryCoreSim:
    """A `CoreSimStore` over dictionaries, with the same answers as the Postgres one."""

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self._policies: dict[str, Policy] = {
            policy.policy_number: policy for policy in SEED_POLICIES
        }
        self._claims: dict[UUID, Claim] = {}
        self._documents: dict[UUID, ClaimDocument] = {}
        self._parties: dict[UUID, ClaimParty] = {}
        self._remembered: dict[str, Remembered] = {}

    @property
    def claims(self) -> tuple[Claim, ...]:
        """What the surrounding systems hold, for tests that assert on the outcome."""
        return tuple(self._claims.values())

    def ready(self) -> bool:
        return self._ready

    def read_policy(self, policy_number: str) -> Policy:
        try:
            return self._policies[policy_number]
        except KeyError:
            raise PolicyNotFound(f"unknown policy {policy_number}") from None

    def create(self, submission: ClaimSubmission, key: str) -> Claim:
        def perform() -> Claim:
            self.read_policy(submission.policy_number)
            claim = Claim(
                claim_id=uuid4(),
                status=ClaimStatus.RECEIVED,
                status_history=[
                    ClaimStatusTransition(
                        status=ClaimStatus.RECEIVED, recorded_at=datetime.now(UTC)
                    )
                ],
                **submission.model_dump(),
            )
            self._claims[claim.claim_id] = claim
            return claim

        return self._once("create_claim", submission.model_dump(mode="json"), key, perform)

    def read(self, claim_id: UUID) -> Claim:
        try:
            return self._claims[claim_id]
        except KeyError:
            raise ClaimNotFound(f"unknown claim {claim_id}") from None

    def record_status(self, claim_id: UUID, status: ClaimStatus, key: str) -> Claim:
        def perform() -> Claim:
            claim = self.read(claim_id)
            updated = claim.model_copy(
                update={
                    "status": status,
                    "status_history": [
                        *claim.status_history,
                        ClaimStatusTransition(status=status, recorded_at=datetime.now(UTC)),
                    ],
                }
            )
            self._claims[claim_id] = updated
            return updated

        request = {"claim_id": str(claim_id), "status": status}
        return self._once("record_claim_status", request, key, perform)

    def attach_document(
        self, claim_id: UUID, attachment: ClaimDocumentSubmission, key: str
    ) -> ClaimDocument:
        def perform() -> ClaimDocument:
            self.read(claim_id)
            document = ClaimDocument(
                document_id=uuid4(),
                claim_id=claim_id,
                attached_at=datetime.now(UTC),
                **attachment.model_dump(),
            )
            self._documents[document.document_id] = document
            return document

        request = {"claim_id": str(claim_id), **attachment.model_dump(mode="json")}
        return self._once("attach_claim_document", request, key, perform)

    def read_parties(self, claim_id: UUID) -> tuple[ClaimParty, ...]:
        self.read(claim_id)
        return tuple(party for party in self._parties.values() if party.claim_id == claim_id)

    def record_party(
        self, claim_id: UUID, registration: ClaimPartySubmission, key: str
    ) -> ClaimParty:
        def perform() -> ClaimParty:
            self.read(claim_id)
            party = ClaimParty(
                party_id=uuid4(),
                claim_id=claim_id,
                recorded_at=datetime.now(UTC),
                **registration.model_dump(),
            )
            self._parties[party.party_id] = party
            return party

        request = {"claim_id": str(claim_id), **registration.model_dump(mode="json")}
        return self._once("record_claim_party", request, key, perform)

    def _once[T](
        self, operation: str, request: Mapping[str, object], key: str, perform: Callable[[], T]
    ) -> T:
        """Run one write once per key, as the transaction in the store does."""
        fingerprint = request_fingerprint(operation, request)
        remembered = self._remembered.get(key)
        if remembered is not None:
            if remembered["fingerprint"] != fingerprint:
                raise IdempotencyKeyReuse(
                    f"Idempotency-Key {key} was already used for a different request"
                )
            return cast("T", remembered["response"])
        written = perform()
        self._remembered[key] = {"fingerprint": fingerprint, "response": written}
        return written


def free_port() -> int:
    """A port nothing is listening on right now, for a test to serve an app on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until_listening(port: int) -> None:
    """Wait until something accepts connections on `port`, failing rather than sleeping forever."""
    deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(LISTEN_POLL_SECONDS)
    raise AssertionError(f"nothing listened on 127.0.0.1:{port} within {STARTUP_DEADLINE_SECONDS}s")


class InMemoryTriageStore:
    """A `TriageStore` over dictionaries, with the Postgres one's transaction semantics.

    The transaction is real in the only way a dictionary can hold one: everything it writes is taken
    back when anything inside it raises, so a test can force a failure between the state change and
    the audit entry and observe that neither survives. The chain is appended through the same
    function the Postgres store appends through, so the two cannot disagree about what an entry
    hashes to.
    """

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self._runs: dict[UUID, TriageRun] = {}
        self._entries: list[AuditEntry] = []

    @property
    def runs(self) -> tuple[TriageRun, ...]:
        """What has been recorded, for tests that assert on the outcome."""
        return tuple(self._runs.values())

    def ready(self) -> bool:
        return self._ready

    def read_run(self, run_id: UUID) -> TriageRun | None:
        return self._runs.get(run_id)

    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    @contextmanager
    def transaction(self) -> Iterator[InMemoryTriageTransaction]:
        """Everything this transaction writes, or nothing: what the store's port promises."""
        held = (dict(self._runs), list(self._entries))
        try:
            yield InMemoryTriageTransaction(self)
        except BaseException:
            self._runs, self._entries = held
            raise

    def _record(self, run: TriageRun) -> None:
        if run.run_id in self._runs:
            raise TriageStoreUnavailable(f"run {run.run_id} is already recorded")
        self._runs[run.run_id] = run

    def _append(self, content: AuditEntryContent) -> AuditEntry:
        last = None if not self._entries else (self._entries[-1].seq, self._entries[-1].hash)
        seq, prev_hash = audit.continues(last)
        entry = audit.append(prev_hash, seq, content)
        self._entries.append(entry)
        return entry


class InMemoryTriageTransaction:
    """One transaction over the dictionary store: both writes, or neither."""

    def __init__(self, store: InMemoryTriageStore) -> None:
        self._store = store

    def record_run(self, run: TriageRun) -> None:
        self._store._record(run)

    def append_entry(self, content: AuditEntryContent) -> AuditEntry:
        return self._store._append(content)


PDF: Final = "application/pdf"
ZIP: Final = "application/zip"
"""The two content types the ingress takes, as the tests declare them."""

ENCRYPTED_FLAG: Final = 0x1
"""The general purpose bit that says a zip entry is encrypted (APPNOTE 4.4.4)."""

LOCAL_FLAG_OFFSET: Final = 6
CENTRAL_FLAG_OFFSET: Final = 8
"""Where that bit sits in a local file header and in a central directory header."""

TEST_GUARD: Final = GuardSettings(
    media_types=frozenset({PDF, ZIP}),
    max_submission_bytes=8192,
    max_document_bytes=4096,
    max_document_pages=3,
    max_archive_expansion_ratio=100.0,
    rate_limit_burst=6,
    rate_limit_refill_per_second=1.0,
)
"""A skeleton's guard thresholds: small enough that a test's document is small too, and its burst is
a number a test can spend. Every field is set, so nothing here depends on an ambient variable."""


def pdf(pages: int) -> bytes:
    """A PDF with as many blank pages as a test needs, so a page ceiling can be a small number."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    written = BytesIO()
    writer.write(written)
    return written.getvalue()


def encrypted_pdf() -> bytes:
    """A PDF that cannot be read without its password, which is what the ingress refuses."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("a password the uploader did not send")
    written = BytesIO()
    writer.write(written)
    return written.getvalue()


def zip_of(entries: Mapping[str, bytes]) -> bytes:
    """A zip holding exactly what a test declares, compressed as a caller would compress it."""
    written = BytesIO()
    with ZipFile(written, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return written.getvalue()


def zip_bomb(expanded: int = 512 * 1024) -> bytes:
    """A zip bomb: a few hundred bytes that declare half a megabyte of nothing."""
    return zip_of({"scan.bin": b"\0" * expanded})


def zip_marked_encrypted(entries: Mapping[str, bytes]) -> bytes:
    """A zip whose entries carry the encryption flag a password-protected archive sets.

    `zipfile` cannot write an encrypted archive, so the flag is set where the format puts it — the
    general purpose bit of the local header and of the central directory — which is what the ingress
    reads. Nothing is decrypted at the boundary, so the flag is the whole of what it can know.
    """
    marked = bytearray(zip_of(entries))
    headers = ((b"PK\x03\x04", LOCAL_FLAG_OFFSET), (b"PK\x01\x02", CENTRAL_FLAG_OFFSET))
    for marker, bit_at in headers:
        start = marked.index(marker) + bit_at
        flag = int.from_bytes(marked[start : start + 2], "little") | ENCRYPTED_FLAG
        marked[start : start + 2] = flag.to_bytes(2, "little")
    return bytes(marked)


def pdf_with_a_lost_page_object() -> bytes:
    """A PDF whose page object has no body, so its page tree cannot be resolved.

    The reader opens it — the cross-reference and the trailer are intact — and fails when the pages
    are resolved, which is what a truncated or garbled document does, and the failure a guard that
    trusted the reader to fail only early would turn into a 500.
    """
    whole = pdf(1)
    page_object = _page_object_number(whole)
    header = whole.index(f"{page_object} 0 obj".encode())
    emptied = whole[:header] + f"{page_object} 0 obj\nendobj\n".encode()
    return emptied + whole[whole.index(b"endobj", header) + len(b"endobj") :]


def pdf_with_no_pages() -> bytes:
    """A PDF whose page tree names a page that is not in the file: it opens, and holds nothing.

    The corruption a truncated download produces, and one the reader reports as an empty document
    rather than as a failure — which is why the ingress refuses it itself.
    """
    whole = pdf(1)
    page_object = _page_object_number(whole)
    return whole.replace(f"/Kids [ {page_object} 0 R ]".encode(), b"/Kids [ 99 0 R ]", 1)


def _page_object_number(whole: bytes) -> str:
    """Which object the page tree points at, so a test can corrupt that one and no other."""
    page_tree = re.search(rb"/Kids\s*\[\s*(\d+)\s+0\s+R", whole)
    assert page_tree is not None, whole
    return page_tree.group(1).decode()


def upload(
    content: bytes, *, media_type: str = PDF, filename: str = "oznamenie-skody.pdf"
) -> Upload:
    """One document as it arrives at the boundary, for the tests that never serve one."""
    return Upload(filename=filename, media_type=media_type, content=content)


CLAIM: dict[str, object] = {
    "policy_number": "SIM-2026-0001",
    "incident_date": "2026-03-14",
    "claim_amount_eur": "1840.50",
}
"""A claim against one of the policies the systems hold, as the entry point takes it."""


@dataclass(frozen=True, slots=True)
class Skeleton:
    """A walking skeleton, wired the way the stack wires it, and the pieces to assert on."""

    api_url: str
    systems: InMemoryCoreSim
    triage: InMemoryTriageStore
    exporter: InMemorySpanExporter
    _traces: tuple[Telemetry, ...] = field(default_factory=tuple)

    def submit(self, **claim: object) -> httpx2.Response:
        """Post a claim at the entry point, and answer with whatever the boundary answered."""
        return self.upload(**claim)

    def upload(self, *documents: Upload, **claim: object) -> httpx2.Response:
        """Post a claim with documents attached, which is what the ingress screens."""
        with httpx2.Client(base_url=self.api_url) as client:
            return client.post(
                "/claims",
                data={"claim": json.dumps({**CLAIM, **claim})},
                files=[
                    ("documents", (document.filename, document.content, document.media_type))
                    for document in documents
                ],
            )

    def submit_ok(self, **claim: object) -> RunResult:
        """Post a claim the skeleton is expected to carry, and parse the run it answered with."""
        response = self.submit(**claim)
        assert response.status_code == 201, response.text
        return RunResult.model_validate(response.json())

    def upload_ok(self, *documents: Upload, **claim: object) -> RunResult:
        """Post a claim with documents the skeleton is expected to carry, and parse the run."""
        response = self.upload(*documents, **claim)
        assert response.status_code == 201, response.text
        return RunResult.model_validate(response.json())

    def readiness(self) -> httpx2.Response:
        """What the entry point answers when asked whether it can serve right now."""
        with httpx2.Client(base_url=self.api_url) as client:
            return client.get("/healthz")

    def drained(self) -> tuple[ReadableSpan, ...]:
        """Every span the run produced: they are batched, so this is what flushes them out."""
        for traces in self._traces:
            traces.flush()
        return tuple(self.exporter.get_finished_spans())
