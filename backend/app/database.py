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
    Безопасная миграция Render/PostgreSQL и локального SQLite.
    Ничего не удаляет. Только добавляет недостающие поля.
    """
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    dialect = engine.dialect.name
    json_type = "JSON" if dialect == "postgresql" else "TEXT"
    bool_type = "BOOLEAN DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN DEFAULT 0"
    int_type = "INTEGER"
    dt_type = "TIMESTAMP"

    review_columns = {
        "platform": "VARCHAR(32)",
        "external_id": "VARCHAR(128)",
        "sku": "VARCHAR(128)",
        "product_name": "TEXT",
        "rating": int_type,
        "text": "TEXT",
        "pros": "TEXT",
        "cons": "TEXT",
        "client_name": "TEXT",
        "created_at_marketplace": dt_type,
        "has_answer": bool_type,
        "raw": json_type,
        "source_status": "VARCHAR(64)",
        "operational_status": "VARCHAR(64)",
        "last_seen_source": "VARCHAR(64)",
        "last_seen_sync_run_id": "VARCHAR(128)",
        "last_seen_at": dt_type,
        "publish_blocked_reason": "TEXT",
        "response_origin": "VARCHAR(32)",
        "ai_category": "VARCHAR(64)",
        "ai_sentiment": "VARCHAR(32)",
        "ai_risk_level": "VARCHAR(32)",
        "ai_can_autopublish": bool_type,
        "ai_reason": "TEXT",
        "ai_tags": json_type,
        "draft_answer": "TEXT",
        "final_answer": "TEXT",
        "status": "VARCHAR(32) DEFAULT 'new'",
        "created_at": dt_type,
        "updated_at": dt_type,
    }

    question_columns = {
        "platform": "VARCHAR(32)",
        "external_id": "VARCHAR(128)",
        "sku": "VARCHAR(128)",
        "product_name": "TEXT",
        "text": "TEXT",
        "client_name": "TEXT",
        "created_at_marketplace": dt_type,
        "has_answer": bool_type,
        "raw": json_type,
        "source_status": "VARCHAR(64)",
        "operational_status": "VARCHAR(64)",
        "last_seen_source": "VARCHAR(64)",
        "last_seen_sync_run_id": "VARCHAR(128)",
        "last_seen_at": dt_type,
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
        "created_at": dt_type,
        "updated_at": dt_type,
    }

    rating_snapshot_columns = {
        "platform": "VARCHAR(32)",
        "sku": "VARCHAR(128)",
        "product_name": "TEXT",
        "rating": "VARCHAR(32)",
        "feedbacks_count": int_type,
        "raw": json_type,
        "created_at": dt_type,
    }


    operation_columns = {
        "platform": "VARCHAR(32)",
        "operation_type": "VARCHAR(64)",
        "external_id": "VARCHAR(128)",
        "document_number": "VARCHAR(128)",
        "sku": "VARCHAR(128)",
        "product_name": "TEXT",
        "warehouse": "TEXT",
        "amount": "VARCHAR(64)",
        "quantity": int_type,
        "reason": "TEXT",
        "status": "VARCHAR(64) DEFAULT 'new'",
        "source_status": "VARCHAR(128)",
        "workflow_status": "VARCHAR(64) DEFAULT 'new'",
        "responsible": "VARCHAR(128)",
        "comment": "TEXT",
        "raw": json_type,
        "occurred_at": dt_type,
        "created_at": dt_type,
        "updated_at": dt_type,
    }

    with engine.begin() as conn:
        for col, col_type in review_columns.items():
            _add_column_if_missing(conn, "reviews", col, col_type)

        for col, col_type in question_columns.items():
            _add_column_if_missing(conn, "questions", col, col_type)

        for col, col_type in rating_snapshot_columns.items():
            _add_column_if_missing(conn, "rating_snapshots", col, col_type)

        for col, col_type in operation_columns.items():
            _add_column_if_missing(conn, "marketplace_operations", col, col_type)

        conn.execute(text("UPDATE reviews SET status = 'new' WHERE status IS NULL"))
        conn.execute(text("UPDATE questions SET status = 'new' WHERE status IS NULL"))

        conn.execute(text("UPDATE reviews SET ai_can_autopublish = FALSE WHERE ai_can_autopublish IS NULL"))
        conn.execute(text("UPDATE questions SET ai_can_autopublish = FALSE WHERE ai_can_autopublish IS NULL"))

    return {"ok": True, "dialect": dialect}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()