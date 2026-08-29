from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from urllib.parse import unquote

from sqlalchemy.engine import make_url

from research_map_backend.settings import Settings


class DataOperationError(RuntimeError):
    pass


def backup_data(settings: Settings, destination: Path | None = None) -> Path:
    data_dir = settings.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = _sqlite_database_path(settings)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (
        destination.resolve()
        if destination is not None
        else (data_dir / "backups" / f"research-map-{stamp}.tar.gz").resolve()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="research-map-backup-") as raw_temp:
        stage = Path(raw_temp) / "data"
        stage.mkdir()
        for source in data_dir.rglob("*"):
            if not source.is_file() or source == output:
                continue
            relative = source.relative_to(data_dir)
            if relative.parts and relative.parts[0] == "backups":
                continue
            if database_path is not None and source.resolve() in {
                database_path,
                Path(f"{database_path}-wal"),
                Path(f"{database_path}-shm"),
            }:
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if database_path is not None and database_path.exists():
            target_db = stage / database_path.relative_to(data_dir)
            target_db.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(database_path) as source_db, sqlite3.connect(target_db) as target:
                source_db.backup(target)
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "database": (
                str(database_path.relative_to(data_dir))
                if database_path is not None and database_path.exists()
                else None
            ),
        }
        (stage / "backup-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        with tarfile.open(output, "w:gz") as archive:
            for item in sorted(stage.rglob("*")):
                archive.add(item, arcname=item.relative_to(stage), recursive=False)
    return output


def restore_data(settings: Settings, archive_path: Path, *, force: bool) -> Path:
    if not force:
        raise DataOperationError("Restore requires --force after services are stopped")
    source = archive_path.resolve()
    if not source.is_file():
        raise DataOperationError(f"Backup does not exist: {source}")
    data_dir = settings.data_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="research-map-restore-") as raw_temp:
        extracted = Path(raw_temp) / "data"
        extracted.mkdir()
        with tarfile.open(source, "r:gz") as archive:
            for member in archive.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise DataOperationError("Backup contains an unsafe path")
                if member.issym() or member.islnk():
                    raise DataOperationError("Backup contains an unsupported link")
            archive.extractall(extracted, filter="data")
        manifest_path = extracted / "backup-manifest.json"
        if not manifest_path.is_file():
            raise DataOperationError("Backup manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != 1:
            raise DataOperationError("Backup format is not supported")
        database_name = manifest.get("database")
        if isinstance(database_name, str):
            restored_database = extracted / database_name
            if not restored_database.is_file():
                raise DataOperationError("Backup database is missing")
            with sqlite3.connect(restored_database) as database:
                if database.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise DataOperationError("Backup database failed its integrity check")
        manifest_path.unlink()
        safety = data_dir.parent / f"{data_dir.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        if data_dir.exists():
            data_dir.rename(safety)
        try:
            shutil.copytree(extracted, data_dir)
        except Exception:
            shutil.rmtree(data_dir, ignore_errors=True)
            if safety.exists():
                safety.rename(data_dir)
            raise
    return safety


def _sqlite_database_path(settings: Settings) -> Path | None:
    url = make_url(settings.resolved_database_url)
    if url.drivername != "sqlite":
        raise DataOperationError("Backup and restore currently support local SQLite only")
    if not url.database or url.database == ":memory:":
        return None
    path = Path(unquote(url.database))
    return path.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-map-data", description="Back up or restore local research-map data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="Create a consistent .data archive.")
    backup.add_argument("--output", type=Path)
    restore = commands.add_parser("restore", help="Restore a validated .data archive.")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "backup":
            print(backup_data(Settings(), args.output))
        else:
            safety = restore_data(Settings(), args.archive, force=args.force)
            print(f"Restored data. Previous data retained at {safety}")
    except (DataOperationError, OSError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
