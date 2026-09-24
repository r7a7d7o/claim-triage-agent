"""The sets, the answers and the arithmetic: what a run reads, and what it counts.

The harness's formats are what this ticket commits to the increments that fill them, so they are
held from the outside: a set is written here as JSON lines, read back through `read_set`, and every
shape the reader refuses has a test that states what it refuses and which line it names. The
answers a run scores are read the same way, and refused on the same terms when they are about cases
the set does not hold.

The arithmetic behind the metrics is checked on the cases a scorer cannot get away from — a value
the document wrote differently, a field the case asserts is absent, a case nothing answered, a
citation of the right clause on the wrong page, a score that orders nothing — because each of those
is a decision about what a number means rather than an implementation detail. A metric that is
silently zero, or silently absent, is the failure mode this module exists to prevent.

Ticket 07's committed sets are empty placeholders; what these tests hold to account is the machinery
that reads, scores and judges them, and the fixture material beside them in `evaluation/fixtures/`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import pytest

from claim_triage.evaluation.answers import read_answers
from claim_triage.evaluation.material import Capability, Unreadable
from claim_triage.evaluation.metrics import (
    Counts,
    average_precision,
    citation_counts,
    compare,
    field_counts,
    hit_at,
    label_counts,
    macro_average,
    reciprocal_rank,
)
from claim_triage.evaluation.scoring import field_metric, is_metric, measure
from claim_triage.evaluation.sets import (
    ClauseRef,
    ExtractionCase,
    Sets,
    read_set,
    read_sets,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

EXTRACTION_HEADER: Final[Mapping[str, Any]] = {
    "capability": "extraction",
    "version": 3,
    "description": "written by a test",
}
RETRIEVAL_HEADER: Final[Mapping[str, Any]] = {
    "capability": "retrieval",
    "version": 1,
    "description": "a test's set",
}
CLASSIFICATION_HEADER: Final[Mapping[str, Any]] = {
    "capability": "classification",
    "version": 1,
    "description": "a test's set",
}
"""One set header per capability: extraction carries a version, so a version is read back."""

NOTIFICATION: Final[Mapping[str, Any]] = {
    "case_id": "synthetic-notification-0001",
    "document": "synthetic-notification-0001",
    "fields": {"policy_number": "SIM-2026-0001", "claim_amount_eur": "1840.50"},
}
"""One extraction case, with the two fields the case states."""

RETRIEVAL_CASE: Final[Mapping[str, Any]] = {
    "case_id": "retrieval-0001",
    "question": "Kryje poistenie škodu, ak vozidlo nemalo platnú technickú kontrolu?",
    "incident_date": "2026-03-14",
    "product_family": "auto-pohoda",
    "expected": [{"clause_id": "USK/PVO/24-4.2", "edition": "USK/PVO/24", "page": 7}],
}
CLASSIFICATION_CASE: Final[Mapping[str, Any]] = {
    "case_id": "classification-0001",
    "claim": {"claim_amount_eur": "1840.50"},
    "fraud_positive": False,
    "fraud_risk": "low",
    "severity": "minor",
    "queue": "fast-lane",
}
"""One case for each of the other two capabilities, so a directory can hold all three."""

CLAUSE: Final = ClauseRef(clause_id="USK/PVO/24-4.2", edition="USK/PVO/24", page=7)
"""The clause the retrieval case expects: what a rank and a citation are read against."""


def write_lines(path: Path, *records: Mapping[str, Any]) -> Path:
    """Write one JSON record per line, the way a committed set and a recorded answer both are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def the_three_sets(
    directory: Path,
    *,
    extraction: tuple[Mapping[str, Any], ...] = (),
    retrieval: tuple[Mapping[str, Any], ...] = (),
    classification: tuple[Mapping[str, Any], ...] = (),
) -> Sets:
    """Write a sets directory — one header per capability, plus whatever cases a test asks for."""
    write_lines(directory / "extraction.jsonl", EXTRACTION_HEADER, *extraction)
    write_lines(directory / "retrieval.jsonl", RETRIEVAL_HEADER, *retrieval)
    write_lines(directory / "classification.jsonl", CLASSIFICATION_HEADER, *classification)
    return read_sets(directory)


def test_a_set_reads_its_header_and_its_cases(tmp_path: Path) -> None:
    """The header decides the capability and the version; the lines are the cases it holds."""
    path = write_lines(tmp_path / "extraction.jsonl", EXTRACTION_HEADER, NOTIFICATION)

    golden = read_set(path, ExtractionCase)

    assert golden.capability is Capability.EXTRACTION
    assert golden.version == 3
    assert golden.description == "written by a test"
    assert [case.case_id for case in golden.cases] == ["synthetic-notification-0001"]
    assert golden.cases[0].fields == {
        "policy_number": "SIM-2026-0001",
        "claim_amount_eur": "1840.50",
    }


def test_a_header_naming_another_capability_is_refused(tmp_path: Path) -> None:
    """A file and its header disagreeing is the mistake that would score one set as another."""
    path = write_lines(
        tmp_path / "extraction.jsonl", {**EXTRACTION_HEADER, "capability": "retrieval"}
    )

    with pytest.raises(Unreadable, match=r"extraction.jsonl:1 declares retrieval"):
        read_set(path, ExtractionCase)


def test_an_unknown_key_is_refused_naming_the_line(tmp_path: Path) -> None:
    """A case with a key the format does not carry is a typo, not a case with an extra field."""
    path = write_lines(
        tmp_path / "extraction.jsonl", EXTRACTION_HEADER, {**NOTIFICATION, "note": "?"}
    )

    with pytest.raises(Unreadable, match=r"extraction.jsonl:2 is not a ExtractionCase"):
        read_set(path, ExtractionCase)


def test_a_field_that_is_not_a_field_name_is_refused(tmp_path: Path) -> None:
    """A field name becomes part of a metric's name, so it has to be a name a metric can carry."""
    path = write_lines(
        tmp_path / "extraction.jsonl",
        EXTRACTION_HEADER,
        {**NOTIFICATION, "fields": {"Claim Amount": 12}},
    )

    with pytest.raises(Unreadable, match=r"'Claim Amount' is not a field name"):
        read_set(path, ExtractionCase)


def test_two_cases_with_one_identifier_are_refused(tmp_path: Path) -> None:
    """A case scored twice would weigh twice, so the second one is refused."""
    path = write_lines(tmp_path / "extraction.jsonl", EXTRACTION_HEADER, NOTIFICATION, NOTIFICATION)

    with pytest.raises(Unreadable, match=r"holds 'synthetic-notification-0001' twice"):
        read_set(path, ExtractionCase)


def test_a_case_that_is_not_json_is_refused_naming_the_line(tmp_path: Path) -> None:
    """A half-written line is refused where it is, rather than dropped from the run."""
    path = tmp_path / "extraction.jsonl"
    path.write_text(json.dumps(EXTRACTION_HEADER) + "\nnot json\n", encoding="utf-8")

    with pytest.raises(Unreadable, match=r"extraction.jsonl:2"):
        read_set(path, ExtractionCase)


def test_a_set_saved_in_another_encoding_is_refused(tmp_path: Path) -> None:
    """Encoding is a real failure in this domain: the corpus holds a document whose mojibake is
    repaired during ingestion, so a set that is not UTF-8 is refused here rather than scored."""
    path = tmp_path / "extraction.jsonl"
    path.write_bytes(
        json.dumps(EXTRACTION_HEADER).encode("utf-8")
        + b"\n"
        + "Kone\u010dn\u00e1".encode("cp1250")
        + b"\n"
    )

    with pytest.raises(Unreadable, match="cannot be read"):
        read_set(path, ExtractionCase)


def test_a_set_file_with_no_line_at_all_is_refused(tmp_path: Path) -> None:
    """A placeholder is a header and no case; a file with nothing in it has not even said that."""
    path = tmp_path / "extraction.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(Unreadable, match="is empty: a set states its capability and version first"):
        read_set(path, ExtractionCase)


def test_a_directory_holding_no_set_for_a_capability_is_refused(tmp_path: Path) -> None:
    """A run scores all three capabilities: a missing file is a run that cannot be made."""
    write_lines(tmp_path / "extraction.jsonl", EXTRACTION_HEADER)
    write_lines(tmp_path / "retrieval.jsonl", RETRIEVAL_HEADER)

    with pytest.raises(Unreadable, match=r"holds no classification.jsonl"):
        read_sets(tmp_path)


def test_a_file_in_the_sets_directory_that_is_not_a_set_is_refused(tmp_path: Path) -> None:
    """Anything else in the directory is refused by name rather than quietly left unread."""
    the_three_sets(tmp_path)
    write_lines(tmp_path / "notes.jsonl", EXTRACTION_HEADER)

    with pytest.raises(Unreadable, match=r"notes.jsonl is not a set"):
        read_sets(tmp_path)


def test_a_file_in_the_answers_directory_that_is_not_answers_is_refused(tmp_path: Path) -> None:
    """As in a sets directory: a stray file is refused by name rather than left unread."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    write_lines(tmp_path / "answers" / "extraction.jsonl")
    write_lines(tmp_path / "answers" / "notes.jsonl", {"case_id": "x", "answer": {}})

    with pytest.raises(Unreadable, match=r"notes.jsonl is not an answers file"):
        read_answers(tmp_path / "answers", sets)


def test_answers_about_a_case_the_set_does_not_hold_are_refused(tmp_path: Path) -> None:
    """Answers and the set are about the same cases, or the metric is over the wrong population."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    write_lines(
        tmp_path / "answers" / "extraction.jsonl",
        {"case_id": "synthetic-notification-0009", "answer": {"fields": {}}},
    )

    with pytest.raises(Unreadable, match=r"does not hold"):
        read_answers(tmp_path / "answers", sets)


def test_an_answer_naming_a_field_that_is_not_a_field_name_is_refused(tmp_path: Path) -> None:
    """An answer's field names become metric names as a case's do, so they are held alike."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    write_lines(
        tmp_path / "answers" / "extraction.jsonl",
        {
            "case_id": "synthetic-notification-0001",
            "answer": {"fields": {"Claim Amount": "1840.50"}},
        },
    )

    with pytest.raises(Unreadable, match="'Claim Amount' is not a field name"):
        read_answers(tmp_path / "answers", sets)


def test_a_case_answered_twice_is_refused(tmp_path: Path) -> None:
    """Two answers for one case would score an arbitrary one of them, so the second is refused."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    write_lines(
        tmp_path / "answers" / "extraction.jsonl",
        {
            "case_id": "synthetic-notification-0001",
            "answer": {"fields": {"policy_number": "SIM-2026-0001"}},
        },
        {"case_id": "synthetic-notification-0001", "answer": {"fields": {}}},
    )

    with pytest.raises(Unreadable, match="twice"):
        read_answers(tmp_path / "answers", sets)


def test_answers_for_a_capability_the_set_holds_no_case_for_are_refused(tmp_path: Path) -> None:
    """A capability the sets hold nothing for cannot have answered anything."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    write_lines(
        tmp_path / "answers" / "extraction.jsonl",
        {"case_id": NOTIFICATION["case_id"], "answer": {"fields": {}}},
    )
    write_lines(tmp_path / "answers" / "retrieval.jsonl")

    with pytest.raises(Unreadable, match=r"which the sets hold no case for"):
        read_answers(tmp_path / "answers", sets)


def test_a_capability_with_cases_and_no_answers_file_is_refused(tmp_path: Path) -> None:
    """Scoring a capability nothing answered would report zeros the build was never asked for."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    (tmp_path / "answers").mkdir()

    with pytest.raises(Unreadable, match=r"no answers at"):
        read_answers(tmp_path / "answers", sets)


def test_an_answers_file_with_no_line_is_a_build_that_answered_nothing(tmp_path: Path) -> None:
    """A file with no line is not a missing file: it is a run that answered none of the cases."""
    sets = the_three_sets(tmp_path / "golden", extraction=(NOTIFICATION,))
    write_lines(tmp_path / "answers" / "extraction.jsonl")

    answers = read_answers(tmp_path / "answers", sets)

    assert answers.extraction == {}


def test_an_answer_outside_its_shape_is_refused(tmp_path: Path) -> None:
    """A score outside its range is a malformed answer, refused where it is read."""
    sets = the_three_sets(tmp_path / "golden", classification=(CLASSIFICATION_CASE,))
    write_lines(
        tmp_path / "answers" / "classification.jsonl",
        {
            "case_id": "classification-0001",
            "answer": {"fraud_score": 1.5, "fraud_risk": "low", "severity": "minor", "queue": "q"},
        },
    )

    with pytest.raises(Unreadable, match=r"is not a ClassificationAnswer"):
        read_answers(tmp_path / "answers", sets)


def test_a_value_matches_however_the_document_wrote_it() -> None:
    """Case and spacing are not differences; an amount written as a decimal is the same amount."""
    assert compare("SIM-2026-0001", "sim-2026-0001")
    assert compare("Poistná  zmluva", "poistná zmluva")
    assert compare("1840.50", 1840.5)
    assert compare(3120, "3120")
    assert not compare("Konečná", "konecna")
    assert not compare(True, 1)
    assert not compare(None, "SIM-2026-0001")


def test_a_field_the_case_states_as_absent_costs_an_invented_value() -> None:
    """The case asserts the document carries no plate: answering one is wrong, silence is right."""
    assert field_counts({"registration_plate": None}, {}) == Counts()
    assert field_counts({"registration_plate": None}, {"registration_plate": "BL-123XY"}) == Counts(
        false_positives=1, false_negatives=1
    )


def test_a_case_nothing_answered_is_counted_as_a_miss() -> None:
    """An unanswered case is not skipped: every field it states is one that was not read."""
    assert field_counts(NOTIFICATION["fields"], {}) == Counts(false_negatives=2)


def test_a_value_the_build_got_wrong_is_a_false_positive_and_a_miss() -> None:
    """A wrong value is both: nothing right was read, and something wrong was reported."""
    counts = field_counts({"claim_amount_eur": "1840.50"}, {"claim_amount_eur": "1840.60"})

    assert counts == Counts(false_positives=1, false_negatives=1)
    assert counts.precision == 0.0


def test_precision_is_zero_when_nothing_was_predicted_at_all() -> None:
    """A count with no prediction behind it scores zero rather than being left out of the table."""
    assert Counts(false_negatives=3).precision == 0.0
    assert Counts(false_negatives=3).recall == 0.0
    assert Counts().f1 == 0.0


def test_a_wrong_band_is_a_false_positive_for_the_band_it_named() -> None:
    """Precision falls when a claim is banded wrongly: the band it named pays for the mistake."""
    counted = label_counts([("low", "high"), ("high", "high")])

    assert counted["low"] == Counts(false_negatives=1)
    assert counted["high"] == Counts(true_positives=1, false_positives=1)
    # `low` was never named, so its precision is the zero nothing predicted scores: 0.5 and 0.0.
    assert macro_average(counted, lambda counts: counts.precision) == 0.25


def test_a_label_the_cases_never_label_with_is_not_a_class_of_its_own() -> None:
    """A band outside the set's own vocabulary is a miss on its case, not a class in the average."""
    counted = label_counts([("low", "critical"), ("high", "high")])

    assert counted["low"] == Counts(false_negatives=1)
    assert counted["high"] == Counts(true_positives=1)
    assert macro_average(counted, lambda counts: counts.recall) == 0.5


def test_a_retrieval_that_cited_nothing_measures_no_citation_validity(tmp_path: Path) -> None:
    """Nothing cited is not nothing valid: the metric is absent, and coverage is what notices."""
    golden = the_three_sets(tmp_path / "golden", retrieval=(RETRIEVAL_CASE,))
    write_lines(
        tmp_path / "answers" / "retrieval.jsonl",
        {
            "case_id": "retrieval-0001",
            "answer": {
                "retrieved": [{"clause_id": "USK/PVO/24-4.2", "edition": "USK/PVO/24", "page": 7}]
            },
        },
    )
    answers = read_answers(tmp_path / "answers", golden)

    _, retrieval, _ = measure(golden, answers)

    assert "retrieval.hit_rate" in retrieval.metrics
    assert "retrieval.citation_validity" not in retrieval.metrics


def test_a_classification_case_nothing_answered_is_a_miss_and_scores_nothing(
    tmp_path: Path,
) -> None:
    """An unanswered case is a miss on the bands and on routing, and has no score to rank."""
    golden = the_three_sets(
        tmp_path / "golden",
        classification=(
            CLASSIFICATION_CASE,
            {**CLASSIFICATION_CASE, "case_id": "classification-0002", "fraud_positive": True},
        ),
    )
    write_lines(
        tmp_path / "answers" / "classification.jsonl",
        {
            "case_id": "classification-0001",
            "answer": {
                "fraud_score": 0.1,
                "fraud_risk": "low",
                "severity": "minor",
                "queue": "fast-lane",
            },
        },
    )
    answers = read_answers(tmp_path / "answers", golden)

    _, _, classification = measure(golden, answers)

    assert classification.answered == 1
    assert classification.metrics["classification.routing_accuracy"] == 0.5
    assert classification.metrics["classification.fraud_risk.recall"] == 0.5


def test_a_classification_of_one_label_measures_no_precision_recall_area(tmp_path: Path) -> None:
    """A set with no negative case has nothing to rank a positive against: no curve."""
    golden = the_three_sets(
        tmp_path / "golden",
        classification=(
            {
                **CLASSIFICATION_CASE,
                "fraud_positive": True,
                "fraud_risk": "high",
                "queue": "review",
            },
        ),
    )
    write_lines(
        tmp_path / "answers" / "classification.jsonl",
        {
            "case_id": "classification-0001",
            "answer": {
                "fraud_score": 0.9,
                "fraud_risk": "high",
                "severity": "minor",
                "queue": "review",
            },
        },
    )
    answers = read_answers(tmp_path / "answers", golden)

    _, _, classification = measure(golden, answers)

    assert "classification.routing_accuracy" in classification.metrics
    assert "classification.fraud_pr_auc" not in classification.metrics


def test_the_first_k_of_a_ranking_is_what_a_hit_reads() -> None:
    """The same retrieval hits at one depth and misses at a shallower one."""
    ranked = [
        ClauseRef(clause_id="USK/PVO/24-3.4", edition="USK/PVO/24"),
        ClauseRef(clause_id="USK/PVO/24-9.1", edition="USK/PVO/24"),
        CLAUSE,
    ]

    assert not hit_at(ranked, [CLAUSE], 2)
    assert hit_at(ranked, [CLAUSE], 3)


def test_a_clause_of_another_edition_is_not_the_clause_the_case_expects() -> None:
    """The point of the retrieval set: a clause under another edition answers nothing."""
    elsewhere = ClauseRef(clause_id=CLAUSE.clause_id, edition="USK/PVO/21", page=6)

    assert not hit_at([elsewhere], [CLAUSE], 5)
    assert reciprocal_rank([elsewhere], [CLAUSE]) == 0.0


def test_the_reciprocal_rank_is_one_over_where_the_clause_first_appeared() -> None:
    """Ranking a relevant clause second scores a half, and never retrieving one scores nothing."""
    ranked = [
        ClauseRef(clause_id="USK/PVO/24-3.4", edition="USK/PVO/24"),
        CLAUSE,
    ]

    assert reciprocal_rank(ranked, [CLAUSE]) == 0.5
    assert reciprocal_rank([], [CLAUSE]) == 0.0


def test_a_citation_of_a_clause_that_was_not_retrieved_does_not_resolve() -> None:
    """The runtime refuses a fabricated citation; the metric counts the same thing as a failure."""
    fabricated = ClauseRef(clause_id="USK/PVO/24-4.9", edition="USK/PVO/24", page=7)

    assert citation_counts([CLAUSE], [fabricated]) == (0, 1)


def test_a_citation_of_the_right_clause_on_the_wrong_page_does_not_resolve() -> None:
    """A citation a reviewer cannot check is not a resolved one, and both sides carry the page."""
    wrong_page = ClauseRef(clause_id=CLAUSE.clause_id, edition=CLAUSE.edition, page=9)

    assert citation_counts([CLAUSE], [wrong_page]) == (0, 1)
    assert citation_counts([CLAUSE], [CLAUSE]) == (1, 1)


def test_the_precision_recall_area_scores_how_high_the_positives_land() -> None:
    """The area under the precision-recall curve: 1.0 ordered perfectly, and lower for a miss."""
    assert average_precision([0.9, 0.8], [0.2, 0.1]) == 1.0
    assert average_precision([0.88, 0.64], [0.31, 0.02]) == 1.0
    assert average_precision([0.1, 0.4], [0.02, 0.2]) == pytest.approx(0.8333, abs=1e-4)
    assert average_precision([0.5, 0.5], [0.5, 0.5]) == 0.5
    assert average_precision([0.9], []) is None
    assert average_precision([], [0.1]) is None


def test_a_field_metric_is_named_for_the_field_it_measures() -> None:
    """A floor and a baseline are held to this vocabulary, so what it accepts is a rule."""
    assert is_metric(field_metric("claim_amount_eur"))
    assert is_metric("retrieval.citation_validity")
    assert not is_metric("extraction.field.Claim_Amount.f1")
    assert not is_metric("extraction.accuracy")
