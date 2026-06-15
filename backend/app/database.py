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

def run_lightweight_migrations():
    """Add columns used by newer MVP versions without dropping local data."""
    additions = {
        'source_status': 'VARCHAR(64)',
        'operational_status': 'VARCHAR(64)',
        'last_seen_source': 'VARCHAR(64)',
        'last_seen_sync_run_id': 'VARCHAR(128)',
        'last_seen_at': 'TIMESTAMP',
        'publish_blocked_reason': 'TEXT',
        'response_origin': 'VARCHAR(32)',
        'ai_tags': 'JSON' if engine.dialect.name == 'postgresql' else 'TEXT',
    }
    with engine.begin() as conn:
        dialect = engine.dialect.name
        for table in ['reviews', 'questions']:
            for col, col_type in additions.items():
                if dialect == 'postgresql':
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}'))
                elif dialect == 'sqlite':
                    if not _column_exists_sqlite(conn, table, col):
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}'))
        # Backfill answer origin for old databases: rows already imported as answered before v4.0
        # came from the seller cabinet unless they were later marked by this app.
        conn.execute(text("UPDATE reviews SET response_origin = 'auto_app' WHERE response_origin IS NULL AND status = 'auto_published'"))
        conn.execute(text("UPDATE questions SET response_origin = 'auto_app' WHERE response_origin IS NULL AND status = 'auto_published'"))
        conn.execute(text("UPDATE reviews SET response_origin = 'manual_app' WHERE response_origin IS NULL AND (status IN ('published', 'answer_edited_in_wb') OR publish_blocked_reason LIKE '%через приложение%')"))
        conn.execute(text("UPDATE questions SET response_origin = 'manual_app' WHERE response_origin IS NULL AND (status IN ('published', 'answer_edited_in_wb') OR publish_blocked_reason LIKE '%через приложение%')"))
        conn.execute(text("UPDATE reviews SET response_origin = 'seller_cabinet' WHERE response_origin IS NULL AND source_status IN ('wb_answered', 'wb_archive', 'ozon_answered')"))
        conn.execute(text("UPDATE questions SET response_origin = 'seller_cabinet' WHERE response_origin IS NULL AND source_status IN ('wb_answered', 'ozon_answered')"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
