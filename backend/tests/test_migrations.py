import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select
from alembic.script import ScriptDirectory

from research_map_backend.db import Database
from research_map_backend.migrations import _alembic_config, upgrade_database
from research_map_backend.models import Canvas
from research_map_backend.settings import Settings


def test_unversioned_create_all_database_is_adopted_without_losing_canvas(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE canvases (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                name VARCHAR(200) NOT NULL,
                research_goal TEXT NOT NULL,
                build_status VARCHAR(32) NOT NULL,
                batch_count INTEGER NOT NULL,
                viewport JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO canvases (
                id, name, research_goal, build_status, batch_count,
                viewport, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-canvas",
                "Preserved canvas",
                "Keep this research goal",
                "idle",
                0,
                None,
                "2026-08-29 00:00:00",
                "2026-08-29 00:00:00",
            ),
        )
        connection.commit()

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{database_path}",
    )
    pre_alembic = Database(settings)
    pre_alembic.create_schema()
    pre_alembic.dispose()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'alembic_version'"
        ).fetchone() is None
        assert "research_brief" not in {
            row[1] for row in connection.execute("PRAGMA table_info(canvases)")
        }

    upgrade_database(settings)
    upgrade_database(settings)

    migrated = Database(settings)
    try:
        with migrated.session_context() as session:
            canvas = session.scalar(
                select(Canvas).where(Canvas.id == "legacy-canvas")
            )
        assert canvas is not None
        assert canvas.name == "Preserved canvas"
        assert canvas.research_goal == "Keep this research goal"
        assert canvas.research_brief is None
        assert "research_brief" in {
            column["name"] for column in inspect(migrated.engine).get_columns("canvases")
        }
        with migrated.engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == ScriptDirectory.from_config(
                _alembic_config(settings)
            ).get_current_head()
    finally:
        migrated.dispose()


def test_0007_makes_paper_dates_nullable_without_losing_data_or_constraints(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "nullable-paper-dates.sqlite3"
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{database_path}",
    )
    config = _alembic_config(settings)
    command.upgrade(config, "0006")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO papers (
                id, title, normalized_title, authors, year, month, summary, url,
                doi, arxiv_id, semantic_scholar_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "known-paper",
                "Known paper",
                "known paper",
                "[]",
                2020,
                5,
                "summary",
                "https://example.test/known",
                "10.1000/known",
                None,
                None,
                "2026-08-29 00:00:00",
                "2026-08-29 00:00:00",
            ),
        )
        before = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(papers)")}
        assert before["year"] == 1
        assert before["month"] == 1
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        after = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(papers)")}
        assert after["year"] == 0
        assert after["month"] == 0
        assert connection.execute(
            "SELECT title, year, month, doi FROM papers WHERE id = 'known-paper'"
        ).fetchone() == ("Known paper", 2020, 5, "10.1000/known")
        assert any(
            row[1] == "ix_papers_normalized_title"
            for row in connection.execute("PRAGMA index_list(papers)")
        )
        connection.execute(
            """
            INSERT INTO papers (
                id, title, normalized_title, authors, year, month, summary, url,
                doi, arxiv_id, semantic_scholar_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "unknown-paper",
                "Unknown date paper",
                "unknown date paper",
                "[]",
                None,
                None,
                "",
                "https://example.test/unknown",
                None,
                None,
                None,
                "2026-08-29 00:00:00",
                "2026-08-29 00:00:00",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO papers (
                    id, title, normalized_title, authors, year, month, summary,
                    url, doi, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "duplicate-doi",
                    "Duplicate DOI",
                    "duplicate doi",
                    "[]",
                    None,
                    None,
                    "",
                    "https://example.test/duplicate",
                    "10.1000/known",
                    "2026-08-29 00:00:00",
                    "2026-08-29 00:00:00",
                ),
            )
