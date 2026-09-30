from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

# SQLite needs check_same_thread=False because FastAPI uses a thread pool
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)

def enable_sqlite_fk(target_engine):
    """Level 13: SQLite ignores foreign key constraints unless this pragma is
    set on every connection - without it, ondelete="RESTRICT" etc. are no-ops.
    Call this for any sqlite engine, including ones built in tests."""

    @event.listens_for(target_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


if settings.DATABASE_URL.startswith("sqlite"):
    enable_sqlite_fk(engine)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """One session per request; always closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
