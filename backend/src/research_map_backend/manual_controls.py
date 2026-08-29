from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from research_map_backend.db import Database
from research_map_backend.models import (
    Canvas,
    CanvasPaper,
    Evidence,
    Paper,
    Relationship,
    Review,
    UserRevision,
)
from research_map_backend.research_store import REVIEW_SNAPSHOT_KEYS
from research_map_backend.schemas import PaperUpdate, RelationshipEdit, ReviewEdit

_REVIEW_INPUT_KEYS = {
    **{key: key for key in REVIEW_SNAPSHOT_KEYS},
    **{value: key for key, value in REVIEW_SNAPSHOT_KEYS.items()},
}


def edit_paper(
    database: Database, canvas_id: str, paper_id: str, change: PaperUpdate
) -> str:
    values = change.model_dump(exclude_unset=True)
    if not values:
        raise ValueError("Paper update is empty")
    with database.session_context() as session:
        membership, paper = _paper_membership(session, canvas_id, paper_id)
        before = _paper_state(membership)
        metadata = dict(membership.metadata_override or {})
        for key in ("title", "authors", "year", "month", "summary", "link"):
            if key in values:
                metadata[key] = values[key]
        membership.metadata_override = metadata or None
        for key in ("x", "y", "pinned"):
            if key in values:
                setattr(membership, key, values[key])
        revision = _revision(
            canvas_id, "paper", membership.id, "update", before, _paper_state(membership)
        )
        session.add(revision)
        session.commit()
        return revision.id


def edit_review(
    database: Database, canvas_id: str, paper_id: str, change: ReviewEdit
) -> str:
    with database.session_context() as session:
        _paper_membership(session, canvas_id, paper_id)
        current = session.scalar(
            select(Review).where(
                Review.canvas_id == canvas_id,
                Review.paper_id == paper_id,
                Review.is_current.is_(True),
            )
        )
        if current is None:
            raise ValueError("Paper has no review to edit")
        sections = {key: dict(value) for key, value in current.sections.items()}
        for supplied_key, text in change.sections.items():
            key = _REVIEW_INPUT_KEYS.get(supplied_key)
            if key is None or key not in sections:
                raise ValueError(f"Unknown review section: {supplied_key}")
            sections[key]["text"] = text
        next_revision = (
            session.scalar(
                select(func.max(Review.revision)).where(
                    Review.canvas_id == canvas_id, Review.paper_id == paper_id
                )
            )
            or 0
        ) + 1
        current.is_current = False
        stored = Review(
            canvas_id=canvas_id,
            paper_id=paper_id,
            sections=sections,
            confidence=current.confidence,
            author="user",
            revision=next_revision,
            is_current=True,
            protected=True,
        )
        session.add(stored)
        session.flush()
        revision = _revision(
            canvas_id,
            "review",
            paper_id,
            "update",
            {"review_id": current.id},
            {"review_id": stored.id},
        )
        session.add(revision)
        session.commit()
        return revision.id


def edit_relationship(
    database: Database, canvas_id: str, relationship_id: str, change: RelationshipEdit
) -> str:
    values = change.model_dump(exclude_unset=True)
    if not values:
        raise ValueError("Relationship update is empty")
    with database.session_context() as session:
        current = _relationship(session, canvas_id, relationship_id)
        next_revision = (
            session.scalar(
                select(func.max(Relationship.revision)).where(
                    Relationship.canvas_id == canvas_id,
                    Relationship.source_paper_id == current.source_paper_id,
                    Relationship.target_paper_id == current.target_paper_id,
                    Relationship.type == current.type,
                )
            )
            or 0
        ) + 1
        current.is_current = False
        stored = Relationship(
            canvas_id=canvas_id,
            source_paper_id=current.source_paper_id,
            target_paper_id=current.target_paper_id,
            type=current.type,
            label=values.get("label", current.label),
            explanation=values.get("explanation", current.explanation),
            confidence=current.confidence,
            author="user",
            revision=next_revision,
            is_current=True,
            protected=True,
        )
        session.add(stored)
        session.flush()
        for item in session.scalars(
            select(Evidence).where(
                Evidence.canvas_id == canvas_id,
                Evidence.owner_type == "relationship",
                Evidence.owner_id == current.id,
            )
        ):
            session.add(
                Evidence(
                    canvas_id=canvas_id,
                    paper_id=item.paper_id,
                    owner_type="relationship",
                    owner_id=stored.id,
                    source_url=item.source_url,
                    page=item.page,
                    excerpt=item.excerpt,
                )
            )
        revision = _revision(
            canvas_id,
            "relationship",
            current.id,
            "update",
            {"relationship_id": current.id},
            {"relationship_id": stored.id},
        )
        session.add(revision)
        session.commit()
        return revision.id


def delete_paper(database: Database, canvas_id: str, paper_id: str) -> str:
    with database.session_context() as session:
        membership, _ = _paper_membership(session, canvas_id, paper_id)
        if membership.deleted_at is not None:
            raise ValueError("Paper is already deleted")
        before = _paper_state(membership)
        membership.deleted_at = datetime.now(UTC)
        revision = _revision(
            canvas_id, "paper", membership.id, "delete", before, _paper_state(membership)
        )
        session.add(revision)
        session.commit()
        return revision.id


def delete_relationship(
    database: Database, canvas_id: str, relationship_id: str
) -> str:
    with database.session_context() as session:
        relationship = _relationship(session, canvas_id, relationship_id)
        if relationship.deleted_at is not None:
            raise ValueError("Relationship is already deleted")
        relationship.deleted_at = datetime.now(UTC)
        revision = _revision(
            canvas_id,
            "relationship",
            relationship.id,
            "delete",
            {"deleted_at": None},
            {"deleted_at": relationship.deleted_at.isoformat()},
        )
        session.add(revision)
        session.commit()
        return revision.id


def undo(database: Database, canvas_id: str, token: str) -> None:
    with database.session_context() as session:
        revision = session.get(UserRevision, token)
        if revision is None or revision.canvas_id != canvas_id:
            raise ValueError("Undo token does not exist for this canvas")
        if revision.undone_at is not None:
            raise ValueError("Undo token was already used")
        if revision.entity_type == "paper":
            membership = session.get(CanvasPaper, revision.entity_id)
            if membership is None or membership.canvas_id != canvas_id:
                raise ValueError("Paper revision target no longer exists")
            _restore_paper_state(membership, revision.before_state)
        elif revision.entity_type == "review" and revision.action == "update":
            _restore_revision_rows(
                session,
                Review,
                revision.before_state["review_id"],
                revision.after_state["review_id"],
            )
        elif revision.entity_type == "relationship" and revision.action == "update":
            _restore_revision_rows(
                session,
                Relationship,
                revision.before_state["relationship_id"],
                revision.after_state["relationship_id"],
            )
        elif revision.entity_type == "relationship" and revision.action == "delete":
            relationship = session.get(Relationship, revision.entity_id)
            if relationship is None or relationship.canvas_id != canvas_id:
                raise ValueError("Relationship revision target no longer exists")
            relationship.deleted_at = None
        else:
            raise ValueError("Undo operation is unsupported")
        revision.undone_at = datetime.now(UTC)
        session.commit()


def _paper_membership(session: Any, canvas_id: str, paper_id: str) -> tuple[CanvasPaper, Paper]:
    canvas = session.get(Canvas, canvas_id)
    if canvas is None or canvas.deleted_at is not None:
        raise ValueError("Canvas does not exist")
    membership = session.scalar(
        select(CanvasPaper).where(
            CanvasPaper.canvas_id == canvas_id, CanvasPaper.paper_id == paper_id
        )
    )
    paper = session.get(Paper, paper_id)
    if membership is None or paper is None:
        raise ValueError("Paper does not belong to the canvas")
    return membership, paper


def _relationship(session: Any, canvas_id: str, relationship_id: str) -> Relationship:
    item = session.get(Relationship, relationship_id)
    if item is None or item.canvas_id != canvas_id or not item.is_current:
        raise ValueError("Relationship does not exist for this canvas")
    return item


def _paper_state(membership: CanvasPaper) -> dict[str, Any]:
    return {
        "metadata_override": membership.metadata_override,
        "x": membership.x,
        "y": membership.y,
        "pinned": membership.pinned,
        "deleted_at": membership.deleted_at.isoformat() if membership.deleted_at else None,
    }


def _restore_paper_state(membership: CanvasPaper, state: dict[str, Any]) -> None:
    membership.metadata_override = state.get("metadata_override")
    membership.x = float(state.get("x", 0))
    membership.y = float(state.get("y", 0))
    membership.pinned = bool(state.get("pinned", False))
    deleted_at = state.get("deleted_at")
    membership.deleted_at = (
        datetime.fromisoformat(deleted_at) if isinstance(deleted_at, str) else None
    )


def _restore_revision_rows(session: Any, model: Any, old_id: str, new_id: str) -> None:
    old = session.get(model, old_id)
    new = session.get(model, new_id)
    if old is None or new is None:
        raise ValueError("Revision history no longer exists")
    new.is_current = False
    old.is_current = True


def _revision(
    canvas_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> UserRevision:
    return UserRevision(
        canvas_id=canvas_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before_state=before,
        after_state=after,
    )
