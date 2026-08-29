from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from research_map_backend.settings import PROJECT_ROOT, Settings

RUN_TABLES = {"agent_runs", "agent_run_events"}
RESEARCH_TABLES = {
    "papers",
    "canvas_papers",
    "paper_sources",
    "reviews",
    "evidence",
    "relationships",
    "discovery_decisions",
    "jobs",
}


class LegacyDatabaseError(RuntimeError):
    """An unversioned database does not match a known local schema."""


def upgrade_database(settings: Settings) -> None:
    """Adopt known pre-Alembic local schemas, then migrate to head."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _adopt_known_legacy_schema(settings)
    config = _alembic_config(settings)
    command.upgrade(config, "head")


def _alembic_config(settings: Settings) -> Config:
    config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    config.attributes["settings"] = settings
    return config


def _adopt_known_legacy_schema(settings: Settings) -> None:
    engine = create_engine(settings.resolved_database_url)
    try:
        with engine.begin() as connection:
            schema = inspect(connection)
            tables = set(schema.get_table_names())
            if "canvases" not in tables or _has_version(connection, tables):
                return

            has_run_tables = RUN_TABLES <= tables
            has_research_tables = RESEARCH_TABLES <= tables
            unexpected_partial = bool(tables & RUN_TABLES) != has_run_tables or bool(
                tables & RESEARCH_TABLES
            ) != has_research_tables
            if unexpected_partial or (has_research_tables and not has_run_tables):
                raise LegacyDatabaseError(
                    "The unversioned database has an unknown partial schema; "
                    "migration stopped without changing it."
                )

            canvas_columns = {
                column["name"] for column in schema.get_columns("canvases")
            }
            required_canvas_columns = {
                "id",
                "name",
                "research_goal",
                "build_status",
                "batch_count",
                "viewport",
                "created_at",
                "updated_at",
            }
            if not required_canvas_columns <= canvas_columns:
                raise LegacyDatabaseError(
                    "The unversioned canvases table has an unknown schema; "
                    "migration stopped without changing it."
                )

            revision = "0001"
            if has_run_tables:
                revision = "0002"
            if has_research_tables:
                if "research_brief" not in canvas_columns:
                    if connection.dialect.name != "sqlite":
                        raise LegacyDatabaseError(
                            "Automatic adoption of this hybrid schema is only "
                            "supported for the local SQLite database."
                        )
                    connection.execute(
                        text("ALTER TABLE canvases ADD COLUMN research_brief JSON")
                    )
                revision = "0003"
    finally:
        engine.dispose()

    command.stamp(_alembic_config(settings), revision)


def _has_version(connection: object, tables: set[str]) -> bool:
    if "alembic_version" not in tables:
        return False
    return connection.execute(  # type: ignore[attr-defined]
        text("SELECT version_num FROM alembic_version LIMIT 1")
    ).scalar_one_or_none() is not None


def main() -> None:
    upgrade_database(Settings())
