"""
database/database.py

SQLite storage layer for the FDA Regulatory Intelligence Agent.

Schema:
  documents   — one row per regulation (tracks latest version + metadata)
  versions    — one row per saved version (full content, hash, timestamp)
  change_log  — one row per save event (audit trail: NEW / CHANGED / UNCHANGED)

Replaces: flat .txt files in knowledge_base/ and _change_log.jsonl
"""

import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager
from config.config import DB_PATH


# ── Connection helper ─────────────────────────────────────────────────────────
@contextmanager
def get_conn():
    """Context manager — always closes connection, rolls back on error."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
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
        conn.executescript("""
            -- One row per regulation (e.g. "21CFR820")
            CREATE TABLE IF NOT EXISTS documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                regulation_id   TEXT    NOT NULL UNIQUE,   -- e.g. "21CFR820"
                title           INTEGER,                    -- CFR title number
                part            INTEGER,                    -- CFR part number
                source_url      TEXT,                       -- where it came from
                latest_version_id INTEGER,                  -- FK → versions.id
                first_fetched   TEXT    NOT NULL,
                last_fetched    TEXT    NOT NULL,
                total_versions  INTEGER NOT NULL DEFAULT 0
            );

            -- One row per saved version (full content stored here)
            CREATE TABLE IF NOT EXISTS versions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                regulation_id   TEXT    NOT NULL,           -- FK → documents.regulation_id
                version_number  INTEGER NOT NULL,           -- 1, 2, 3 ...
                content         TEXT    NOT NULL,           -- full regulation text
                content_hash    TEXT    NOT NULL,           -- SHA-256[:16] for change detection
                content_length  INTEGER NOT NULL,           -- char count
                status          TEXT    NOT NULL,           -- NEW | CHANGED | UNCHANGED
                version_note    TEXT,
                saved_at        TEXT    NOT NULL,
                FOREIGN KEY (regulation_id) REFERENCES documents(regulation_id)
            );

            -- One row per save event (audit trail — never deleted)
            CREATE TABLE IF NOT EXISTS change_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                regulation_id   TEXT    NOT NULL,
                status          TEXT    NOT NULL,           -- NEW | CHANGED | UNCHANGED
                content_hash    TEXT    NOT NULL,
                prev_hash       TEXT,                       -- NULL for NEW entries
                version_number  INTEGER,
                version_note    TEXT,
                logged_at       TEXT    NOT NULL
            );

            -- Indexes for fast queries
            CREATE INDEX IF NOT EXISTS idx_versions_regulation
                ON versions(regulation_id);
            CREATE INDEX IF NOT EXISTS idx_versions_saved_at
                ON versions(saved_at DESC);
            CREATE INDEX IF NOT EXISTS idx_change_log_regulation
                ON change_log(regulation_id);
            CREATE INDEX IF NOT EXISTS idx_change_log_status
                ON change_log(status);
            CREATE INDEX IF NOT EXISTS idx_change_log_logged_at
                ON change_log(logged_at DESC);
        """)
    print(f"✅ Database ready: {DB_PATH}")


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
    """
    Save a regulation version. Returns a dict with:
      status        — NEW | CHANGED | UNCHANGED
      version_number
      prev_hash     — previous hash (None if NEW)
      regulation_id
    """
    now = datetime.now().isoformat()

    with get_conn() as conn:
        # ── Check if this regulation exists ──────────────────────────────────
        doc = conn.execute(
            "SELECT * FROM documents WHERE regulation_id = ?", (regulation_id,)
        ).fetchone()

        if doc is None:
            # First time we've seen this regulation
            status         = "NEW"
            prev_hash      = None
            version_number = 1

            conn.execute("""
                INSERT INTO documents
                    (regulation_id, title, part, source_url, first_fetched, last_fetched, total_versions)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (regulation_id, title, part, source_url, now, now))

        else:
            # Get the latest version's hash for comparison
            latest = conn.execute("""
                SELECT content_hash, version_number FROM versions
                WHERE regulation_id = ?
                ORDER BY version_number DESC
                LIMIT 1
            """, (regulation_id,)).fetchone()

            prev_hash = latest["content_hash"] if latest else None

            if prev_hash == content_hash:
                # Content identical — log it, don't write a new version
                status         = "UNCHANGED"
                version_number = latest["version_number"]

                conn.execute("""
                    INSERT INTO change_log
                        (regulation_id, status, content_hash, prev_hash, version_number, version_note, logged_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (regulation_id, "UNCHANGED", content_hash, prev_hash, version_number, version_note, now))

                conn.execute(
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
                version_number = (latest["version_number"] if latest else 0) + 1

        # ── Write new version row ─────────────────────────────────────────────
        cursor = conn.execute("""
            INSERT INTO versions
                (regulation_id, version_number, content, content_hash,
                 content_length, status, version_note, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            regulation_id, version_number, content, content_hash,
            len(content), status, version_note, now
        ))
        version_id = cursor.lastrowid

        # ── Update documents table ────────────────────────────────────────────
        conn.execute("""
            UPDATE documents
            SET latest_version_id = ?,
                last_fetched      = ?,
                total_versions    = total_versions + 1,
                source_url        = COALESCE(NULLIF(?, ''), source_url),
                title             = COALESCE(?, title),
                part              = COALESCE(?, part)
            WHERE regulation_id = ?
        """, (version_id, now, source_url, title, part, regulation_id))

        # ── Write change log row ──────────────────────────────────────────────
        conn.execute("""
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


# ── Read latest version of a regulation ──────────────────────────────────────
def get_latest(regulation_id: str) -> dict | None:
    """Return the latest version row, or None if not found."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT v.*, d.source_url, d.title, d.part
            FROM versions v
            JOIN documents d ON d.regulation_id = v.regulation_id
            WHERE v.regulation_id = ?
            ORDER BY v.version_number DESC
            LIMIT 1
        """, (regulation_id,)).fetchone()
        return dict(row) if row else None


# ── Read a specific version by number ────────────────────────────────────────
def get_version(regulation_id: str, version_number: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM versions
            WHERE regulation_id = ? AND version_number = ?
        """, (regulation_id, version_number)).fetchone()
        return dict(row) if row else None


# ── List all regulations ──────────────────────────────────────────────────────
def list_regulations(filter_str: str = "") -> list[dict]:
    """Return all documents with their latest version info."""
    with get_conn() as conn:
        query = """
            SELECT d.*, v.content_hash, v.content_length, v.saved_at as latest_saved_at,
                   v.version_number as latest_version_number, v.status as latest_status
            FROM documents d
            LEFT JOIN versions v ON v.id = d.latest_version_id
        """
        if filter_str:
            query += " WHERE d.regulation_id LIKE ?"
            rows = conn.execute(query, (f"%{filter_str}%",)).fetchall()
        else:
            rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]


# ── List all versions for a regulation ───────────────────────────────────────
def list_versions(regulation_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, regulation_id, version_number, content_hash,
                   content_length, status, version_note, saved_at
            FROM versions
            WHERE regulation_id = ?
            ORDER BY version_number ASC
        """, (regulation_id,)).fetchall()
        return [dict(r) for r in rows]


# ── Get change log entries ────────────────────────────────────────────────────
def get_change_log(
    filter_str: str = "",
    limit: int = 20,
    changed_only: bool = False,
) -> list[dict]:
    with get_conn() as conn:
        conditions = []
        params     = []

        if filter_str:
            conditions.append("regulation_id LIKE ?")
            params.append(f"%{filter_str}%")
        if changed_only:
            conditions.append("status != 'UNCHANGED'")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        rows = conn.execute(f"""
            SELECT * FROM change_log
            {where}
            ORDER BY logged_at DESC
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]


# ── DB stats (for diagnostics) ────────────────────────────────────────────────
def get_stats() -> dict:
    with get_conn() as conn:
        docs     = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        versions = conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        changes  = conn.execute("SELECT COUNT(*) FROM change_log WHERE status = 'CHANGED'").fetchone()[0]
        db_size  = os.path.getsize(DB_PATH) / 1024 if os.path.exists(DB_PATH) else 0
        return {
            "regulations": docs,
            "total_versions": versions,
            "change_events": changes,
            "db_size_kb": round(db_size, 1),
        }