from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from research_map_backend.models import Base
from research_map_backend.settings import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(settings.resolved_database_url)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._configure_sqlite(self.engine)

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            return

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def check(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def session(self) -> Generator[Session, None, None]:
        with self.session_context() as session:
            yield session

    @contextmanager
    def session_context(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
