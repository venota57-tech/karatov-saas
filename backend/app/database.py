from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def _column_exists_sqlite(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def _column_exists_postgres(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :table
              AND column_name = :column
            LIMIT 1
            """
        ),
        {"table": table, "column": column},
    ).fetchone()
    return row is not None


def _add_column_if_missing(conn, table: str, column: str, column_type: str) -> None:
    dialect = engine.dialect.name

    if dialect == "postgresql":
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}"))
        return

    if dialect == "sqlite":
        if not _column_exists_sqlite(conn, table, column):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))


def run_lightweight_migrations():
    """
    Безопасная миграция для Render/PostgreSQL и локального SQLite.
    Ничего не удаляет, только добавляет недостающие поля.
    """
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    dialect = engine.dialect.name
    json_type = "JSON" if dialect == "postgresql" else "TEXT"
    bool_type = "BOOLEAN DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN DEFAULT 0"

    common_additions = {
        "source_status": "VARCHAR(64)",
        "operational_status": "VARCHAR(64)",
        "last_seen_source": "VARCHAR(64)",
        "last_seen_sync_run_id": "VARCHAR(128)",
        "last_seen_at": "TIMESTAMP",
        "publish_blocked_reason": "TEXT",
        "response_origin": "VARCHAR(32)",
        "ai_category": "VARCHAR(64)",
        "ai_risk_level": "VARCHAR(32)",
        "ai_can_autopublish": bool_type,
        "ai_reason": "TEXT",
        "ai_tags": json_type,
        "draft_answer": "TEXT",
        "final_answer": "TEXT",
        "status": "VARCHAR(32) DEFAULT 'new'",
        "updated_at": "TIMESTAMP",
    }

    review_only_additions = {
        "ai_sentiment": "VARCHAR(32)",
    }

    with engine.begin() as conn:
        for table in ["reviews", "questions"]:
            for col, col_type in common_additions.items():
                _add_column_if_missing(conn, table, col, col_type)

        for col, col_type in review_only_additions.items():
            _add_column_if_missing(conn, "reviews", col, col_type)

        conn.execute(text("UPDATE reviews SET status = 'new' WHERE status IS NULL"))
        conn.execute(text("UPDATE questions SET status = 'new' WHERE status IS NULL"))

        conn.execute(text("UPDATE reviews SET ai_can_autopublish = FALSE WHERE ai_can_autopublish IS NULL"))
        conn.execute(text("UPDATE questions SET ai_can_autopublish = FALSE WHERE ai_can_autopublish IS NULL"))

        conn.execute(text("UPDATE reviews SET response_origin = 'auto_app' WHERE response_origin IS NULL AND status = 'auto_published'"))
        conn.execute(text("UPDATE questions SET response_origin = 'auto_app' WHERE response_origin IS NULL AND status = 'auto_published'"))

        conn.execute(text("UPDATE reviews SET response_origin = 'seller_cabinet' WHERE response_origin IS NULL AND source_status IN ('wb_answered', 'wb_archive', 'ozon_answered')"))
        conn.execute(text("UPDATE questions SET response_origin = 'seller_cabinet' WHERE response_origin IS NULL AND source_status IN ('wb_answered', 'ozon_answered')"))

    return {"ok": True, "dialect": dialect}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()