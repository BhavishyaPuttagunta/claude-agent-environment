"""
database/database.py

SQL Server storage layer for the Regulatory Intelligence Agent.

Schema:
  documents   — one row per saved page (tracks latest version + metadata)
  versions    — one row per saved version (full content, hash, timestamp)
  change_log  — one row per save event (audit trail: NEW / CHANGED / UNCHANGED)
"""

import os
import pyodbc
from datetime import datetime
from contextlib import contextmanager
from config.config import DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD, DB_DRIVER


# ── Connection helper ─────────────────────────────────────────────────────────
def _build_conn_str() -> str:
    return (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=yes;"
    )

@contextmanager
def get_conn():
    conn = pyodbc.connect(_build_conn_str(), timeout=30)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema creation ───────────────────────────────────────────────────────────
def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='documents' AND xtype='U')
            CREATE TABLE documents (
                id                INTEGER IDENTITY(1,1) PRIMARY KEY,
                regulation_id     NVARCHAR(255) NOT NULL UNIQUE,
                title             INTEGER,
                part              INTEGER,
                source_url        NVARCHAR(500),
                latest_version_id INTEGER,
                first_fetched     NVARCHAR(50)  NOT NULL,
                last_fetched      NVARCHAR(50)  NOT NULL,
                total_versions    INTEGER NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='versions' AND xtype='U')
            CREATE TABLE versions (
                id              INTEGER IDENTITY(1,1) PRIMARY KEY,
                regulation_id   NVARCHAR(255) NOT NULL,
                version_number  INTEGER       NOT NULL,
                content         NVARCHAR(MAX) NOT NULL,
                content_hash    NVARCHAR(64)  NOT NULL,
                content_length  INTEGER       NOT NULL,
                status          NVARCHAR(20)  NOT NULL,
                version_note    NVARCHAR(500),
                saved_at        NVARCHAR(50)  NOT NULL
            )
        """)

        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='change_log' AND xtype='U')
            CREATE TABLE change_log (
                id              INTEGER IDENTITY(1,1) PRIMARY KEY,
                regulation_id   NVARCHAR(255) NOT NULL,
                status          NVARCHAR(20)  NOT NULL,
                content_hash    NVARCHAR(64)  NOT NULL,
                prev_hash       NVARCHAR(64),
                version_number  INTEGER,
                version_note    NVARCHAR(500),
                logged_at       NVARCHAR(50)  NOT NULL
            )
        """)

        # Indexes
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_versions_regulation')
            CREATE INDEX idx_versions_regulation ON versions(regulation_id)
        """)
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_change_log_regulation')
            CREATE INDEX idx_change_log_regulation ON change_log(regulation_id)
        """)
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_change_log_status')
            CREATE INDEX idx_change_log_status ON change_log(status)
        """)

        conn.commit()
    print(f"✅ Database ready: {DB_SERVER}/{DB_NAME}")


# ── Row → dict helper ─────────────────────────────────────────────────────────
def _row_to_dict(cursor, row) -> dict:
    return {desc[0]: val for desc, val in zip(cursor.description, row)}


# ── Save / upsert a regulation version ───────────────────────────────────────
def save_regulation(
    regulation_id: str,
    content: str,
    content_hash: str,
    source_url: str = "",
    version_note: str = "",
    title: int = None,
    part: int = None,
) -> dict:
    now = datetime.now().isoformat()

    with get_conn() as conn:
        cursor = conn.cursor()

        # Check if regulation exists
        cursor.execute(
            "SELECT * FROM documents WHERE regulation_id = ?", (regulation_id,)
        )
        doc = cursor.fetchone()

        if doc is None:
            # First save
            status         = "NEW"
            prev_hash      = None
            version_number = 1

            cursor.execute("""
                INSERT INTO documents
                    (regulation_id, title, part, source_url, first_fetched, last_fetched, total_versions)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (regulation_id, title, part, source_url, now, now))

        else:
            # Get latest version hash
            cursor.execute("""
                SELECT TOP 1 content_hash, version_number FROM versions
                WHERE regulation_id = ?
                ORDER BY version_number DESC
            """, (regulation_id,))
            latest = cursor.fetchone()
            prev_hash = latest[0] if latest else None

            if prev_hash == content_hash:
                # Unchanged
                version_number = latest[1]
                cursor.execute("""
                    INSERT INTO change_log
                        (regulation_id, status, content_hash, prev_hash, version_number, version_note, logged_at)
                    VALUES (?, 'UNCHANGED', ?, ?, ?, ?, ?)
                """, (regulation_id, content_hash, prev_hash, version_number, version_note, now))
                cursor.execute(
                    "UPDATE documents SET last_fetched = ? WHERE regulation_id = ?",
                    (now, regulation_id)
                )
                return {
                    "status":         "UNCHANGED",
                    "version_number": version_number,
                    "prev_hash":      prev_hash,
                    "regulation_id":  regulation_id,
                }
            else:
                status         = "CHANGED"
                version_number = (latest[1] if latest else 0) + 1

        # Insert new version
        cursor.execute("""
            INSERT INTO versions
                (regulation_id, version_number, content, content_hash,
                 content_length, status, version_note, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            regulation_id, version_number, content, content_hash,
            len(content), status, version_note, now
        ))

        # Get the new version's ID
        cursor.execute("SELECT @@IDENTITY")
        version_id = int(cursor.fetchone()[0])

        # Update documents
        cursor.execute("""
            UPDATE documents
            SET latest_version_id = ?,
                last_fetched      = ?,
                total_versions    = total_versions + 1,
                source_url        = COALESCE(NULLIF(?, ''), source_url),
                title             = COALESCE(?, title),
                part              = COALESCE(?, part)
            WHERE regulation_id   = ?
        """, (version_id, now, source_url, title, part, regulation_id))

        # Write change log
        cursor.execute("""
            INSERT INTO change_log
                (regulation_id, status, content_hash, prev_hash, version_number, version_note, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (regulation_id, status, content_hash, prev_hash, version_number, version_note, now))

        return {
            "status":         status,
            "version_number": version_number,
            "prev_hash":      prev_hash,
            "regulation_id":  regulation_id,
        }


# ── Read latest version ───────────────────────────────────────────────────────
def get_latest(regulation_id: str) -> dict | None:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 1 v.*, d.source_url, d.title, d.part
            FROM versions v
            JOIN documents d ON d.regulation_id = v.regulation_id
            WHERE v.regulation_id = ?
            ORDER BY v.version_number DESC
        """, (regulation_id,))
        row = cursor.fetchone()
        return _row_to_dict(cursor, row) if row else None


# ── Read specific version ─────────────────────────────────────────────────────
def get_version(regulation_id: str, version_number: int) -> dict | None:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM versions
            WHERE regulation_id = ? AND version_number = ?
        """, (regulation_id, version_number))
        row = cursor.fetchone()
        return _row_to_dict(cursor, row) if row else None


# ── List all saved pages ──────────────────────────────────────────────────────
def list_regulations(filter_str: str = "") -> list[dict]:
    with get_conn() as conn:
        cursor = conn.cursor()
        if filter_str:
            cursor.execute("""
                SELECT d.*, v.content_hash, v.content_length,
                       v.saved_at as last_fetched,
                       v.version_number as latest_version_number,
                       v.status as latest_status
                FROM documents d
                LEFT JOIN versions v ON v.id = d.latest_version_id
                WHERE d.regulation_id LIKE ?
            """, (f"%{filter_str}%",))
        else:
            cursor.execute("""
                SELECT d.*, v.content_hash, v.content_length,
                       v.saved_at as last_fetched,
                       v.version_number as latest_version_number,
                       v.status as latest_status
                FROM documents d
                LEFT JOIN versions v ON v.id = d.latest_version_id
            """)
        rows = cursor.fetchall()
        return [_row_to_dict(cursor, r) for r in rows]


# ── List versions for a regulation ───────────────────────────────────────────
def list_versions(regulation_id: str) -> list[dict]:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, regulation_id, version_number, content_hash,
                   content_length, status, version_note, saved_at
            FROM versions
            WHERE regulation_id = ?
            ORDER BY version_number ASC
        """, (regulation_id,))
        rows = cursor.fetchall()
        return [_row_to_dict(cursor, r) for r in rows]


# ── Get change log ────────────────────────────────────────────────────────────
def get_change_log(
    filter_str: str = "",
    limit: int = 20,
    changed_only: bool = False,
) -> list[dict]:
    with get_conn() as conn:
        cursor = conn.cursor()
        conditions = []
        params     = []

        if filter_str:
            conditions.append("regulation_id LIKE ?")
            params.append(f"%{filter_str}%")
        if changed_only:
            conditions.append("status != 'UNCHANGED'")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        cursor.execute(f"""
            SELECT TOP (?) * FROM change_log
            {where}
            ORDER BY logged_at DESC
        """, [limit] + params[:-1])
        rows = cursor.fetchall()
        return [_row_to_dict(cursor, r) for r in rows]


# ── DB stats ──────────────────────────────────────────────────────────────────
def get_stats() -> dict:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents")
        docs = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM versions")
        versions = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM change_log WHERE status = 'CHANGED'")
        changes = cursor.fetchone()[0]
        return {
            "regulations":    docs,
            "total_versions": versions,
            "change_events":  changes,
            "db_size_kb":     "SQL Server",
        }