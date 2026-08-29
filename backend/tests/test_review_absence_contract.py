import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from research_map_backend.db import Database
from research_map_backend.models import Canvas, CanvasPaper, Paper, Review
from research_map_backend.pipeline import (
    SECTION_KEYS,
    AutonomousPipelineError,
    _validate_review_output,
)
from research_map_backend.research_store import store_review
from research_map_backend.schemas import ReviewInput, states_information_not_reported
from research_map_backend.settings import Settings


ACCEPTED_BY_SECTION = {
    "core_idea": "Not reported in the supplied evidence.",
    "problem_space": "The paper does not report the requested information.",
    "approach": "This paper doesn't provide approach details.",
    "data": "The authors did not specify dataset information.",
    "novel_contribution": "The contribution is not explicitly stated.",
    "results": "No result details were provided.",
    "benchmarks": "No benchmark comparisons have been documented.",
    "statistical_evidence": (
        "No statistical evidence is reported; the discussion remains qualitative."
    ),
    "limitations": "The manuscript does not explicitly mention limitations.",
    "cited_ideas_and_differences": (
        "Citation differences are not described in the supplied evidence."
    ),
    "plain_language_summary": "The paper's overall idea is not reported.",
}


STRICT_NEGATIVES = (
    "No significant difference was reported.",
    "The paper reports no statistically significant effect.",
    "Evidence is reported on page 7.",
    "Not all results were reported.",
    "Reporting is unclear.",
    "This was not reported by prior work.",
    "Prior work does not report this information.",
    "Statistical evidence from previous studies was not reported.",
    "The result was reportedly not significant.",
    "No evidence of improvement was reported.",
)


def _review_body(
    text_by_section: dict[str, str], *, summary_evidence_id: str | None = None
) -> dict:
    return {
        "sections": {
            key: {
                "text": text_by_section[key],
                "evidence_ids": (
                    [summary_evidence_id]
                    if key == "plain_language_summary" and summary_evidence_id
                    else []
                ),
                "confidence": "low",
            }
            for key in SECTION_KEYS
        },
        "confidence": "low",
    }


def test_all_review_sections_share_controlled_absence_clause_contract() -> None:
    body = _review_body(ACCEPTED_BY_SECTION, summary_evidence_id="evidence-1")

    parsed = ReviewInput.model_validate(body)
    validated = _validate_review_output(json.dumps(body), ["evidence-1"])

    assert set(parsed.sections) == set(SECTION_KEYS)
    assert validated["sections"]["statistical_evidence"]["text"].startswith(
        "No statistical evidence is reported"
    )


@pytest.mark.parametrize(
    "text",
    (
        "  NOT   REPORTED in the supplied evidence.  ",
        "This study DOES NOT REPORT confidence intervals.",
        "The authors didn’t provide implementation details.",
        "Statistical tests ARE NOT PRESENTED.",
        "No benchmark details were provided.",
    ),
)
def test_common_absence_equivalents_are_accepted_by_both_validators(text: str) -> None:
    body = _review_body(dict.fromkeys(SECTION_KEYS, text))

    assert states_information_not_reported(text)
    assert ReviewInput.model_validate(body)
    with pytest.raises(AutonomousPipelineError, match="must cite evidence"):
        _validate_review_output(json.dumps(body), [])


@pytest.mark.parametrize("text", STRICT_NEGATIVES)
def test_claims_and_unrelated_prior_work_do_not_satisfy_absence_contract(
    text: str,
) -> None:
    body = _review_body(dict.fromkeys(SECTION_KEYS, text))

    assert not states_information_not_reported(text)
    with pytest.raises(ValidationError, match="not reported"):
        ReviewInput.model_validate(body)
    with pytest.raises(AutonomousPipelineError, match="not reported"):
        _validate_review_output(json.dumps(body), [])


def test_store_review_uses_the_same_absence_contract(tmp_path: Path) -> None:
    database = Database(
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'absence.sqlite3'}",
        )
    )
    database.create_schema()
    with database.session_context() as session:
        canvas = Canvas(name="Absence", research_goal="Map evidence reporting.")
        paper = Paper(
            title="Paper",
            normalized_title="paper",
            authors=[],
            year=2024,
            month=1,
            summary="",
            url="https://example.test/paper",
        )
        session.add_all([canvas, paper])
        session.flush()
        session.add(CanvasPaper(canvas_id=canvas.id, paper_id=paper.id))
        session.commit()
        canvas_id, paper_id = canvas.id, paper.id

    review_id = store_review(
        database,
        canvas_id,
        paper_id,
        _review_body(ACCEPTED_BY_SECTION),
    )
    invalid = dict(ACCEPTED_BY_SECTION)
    invalid["statistical_evidence"] = "No significant difference was reported."
    with pytest.raises(ValueError, match="not reported"):
        store_review(database, canvas_id, paper_id, _review_body(invalid))
    with database.session_context() as session:
        stored = session.scalar(select(Review).where(Review.id == review_id))
    database.dispose()

    assert stored is not None
    assert stored.sections["statistical_evidence"]["evidence_ids"] == []
