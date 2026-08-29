from __future__ import annotations

from sqlalchemy import select

from research_map_backend.db import Database
from research_map_backend.models import Canvas, CanvasPaper
from research_map_backend.schemas import CanvasLayoutUpdate


def update_canvas_layout(
    database: Database, canvas_id: str, payload: CanvasLayoutUpdate
) -> None:
    """Persist a validated layout batch in one transaction."""
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        if canvas is None or canvas.deleted_at is not None:
            raise ValueError("Canvas does not exist")
        active = {
            row.paper_id: row
            for row in session.scalars(
                select(CanvasPaper).where(
                    CanvasPaper.canvas_id == canvas_id,
                    CanvasPaper.deleted_at.is_(None),
                )
            )
        }
        requested_ids = [position.paper_id for position in payload.positions]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("Layout positions contain a duplicate paper")
        missing = sorted(set(requested_ids) - set(active))
        if missing:
            raise ValueError("Every layout paper must belong to the active canvas")
        for position in payload.positions:
            membership = active[position.paper_id]
            membership.x = position.x
            membership.y = position.y
            membership.pinned = position.pinned
        session.commit()


def reset_canvas_layout(database: Database, canvas_id: str) -> None:
    """Unpin active nodes without destroying their last known coordinates."""
    with database.session_context() as session:
        canvas = session.get(Canvas, canvas_id)
        if canvas is None or canvas.deleted_at is not None:
            raise ValueError("Canvas does not exist")
        memberships = session.scalars(
            select(CanvasPaper).where(
                CanvasPaper.canvas_id == canvas_id,
                CanvasPaper.deleted_at.is_(None),
            )
        )
        for membership in memberships:
            membership.pinned = False
        session.commit()
