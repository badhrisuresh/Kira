"""Async Postgres persistence for users, sessions, messages, and productions.

Requires DATABASE_URL env var (e.g. postgresql://user:pass@localhost/kira).
When DATABASE_URL is unset, all operations silently no-op so local dev
without Postgres still works.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import asyncpg

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_MIGRATION = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sessions' AND column_name = 'user_phone'
    ) THEN
        ALTER TABLE sessions RENAME TO wa_sessions;
    END IF;
END
$$;
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    phone       TEXT PRIMARY KEY,
    user_id     TEXT UNIQUE NOT NULL,
    memory      JSONB NOT NULL DEFAULT '{"topics": [], "standing": [], "next": null}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wa_sessions (
    id          TEXT PRIMARY KEY,
    user_phone  TEXT NOT NULL REFERENCES users(phone),
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES wa_sessions(id),
    user_phone  TEXT NOT NULL,
    role        TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS productions (
    id           SERIAL PRIMARY KEY,
    session_id   TEXT REFERENCES wa_sessions(id),
    user_phone   TEXT NOT NULL REFERENCES users(phone),
    block_id     TEXT,
    topic        TEXT,
    gcs_url      TEXT,
    youtube_id   TEXT,
    youtube_url  TEXT,
    status       TEXT DEFAULT 'pending',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS production_assets (
    id             SERIAL PRIMARY KEY,
    production_id  INTEGER REFERENCES productions(id),
    asset_type     TEXT NOT NULL,
    shot_index     INTEGER,
    source_url     TEXT,
    gcs_url        TEXT,
    prompt         TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
"""


def is_enabled() -> bool:
    return bool(DATABASE_URL)


def run_migration_sync(sqlalchemy_url: str) -> None:
    """Run the sessions→wa_sessions migration synchronously.

    Must be called BEFORE ADK's DatabaseSessionService is created, since
    it calls create_all() and would collide with the old sessions table.
    """
    from sqlalchemy import create_engine, text
    engine = create_engine(sqlalchemy_url, connect_args={"sslmode": "require", "connect_timeout": 10})
    with engine.connect() as conn:
        conn.execute(text(_MIGRATION))
        conn.commit()
    engine.dispose()
    log.info("[DB] Synchronous migration complete (sessions → wa_sessions)")


async def init() -> None:
    """Create the connection pool and run schema migration."""
    global _pool
    if not DATABASE_URL:
        log.info("[DB] DATABASE_URL not set — running without Postgres")
        return
    # Parse the URL manually because asyncpg's URL parser mishandles
    # usernames containing dots (e.g. Supabase pooler format
    # "postgres.ref@host").
    from urllib.parse import urlparse
    parsed = urlparse(DATABASE_URL)
    _pool = await asyncpg.create_pool(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/") or "postgres",
        min_size=2,
        max_size=10,
        ssl="require",
        statement_cache_size=0,  # Required for Supabase/pgbouncer transaction mode
        timeout=10,
    )
    async with _pool.acquire() as conn:
        await conn.execute(_MIGRATION)
        await conn.execute(_SCHEMA)
    log.info("[DB] Connected and schema applied")


async def close() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ── Users ────────────────────────────────────────────────────────

async def upsert_user(phone: str, user_id: str) -> None:
    if not _pool:
        return
    await _pool.execute(
        """INSERT INTO users (phone, user_id) VALUES ($1, $2)
           ON CONFLICT (phone) DO NOTHING""",
        phone, user_id,
    )


async def get_user(phone: str) -> Optional[asyncpg.Record]:
    if not _pool:
        return None
    return await _pool.fetchrow("SELECT * FROM users WHERE phone = $1", phone)


async def get_user_memory(phone: str) -> dict:
    if not _pool:
        return {}
    row = await _pool.fetchval(
        "SELECT memory FROM users WHERE phone = $1", phone,
    )
    if row is None:
        return {}
    return json.loads(row) if isinstance(row, str) else row


async def update_user_memory(phone: str, memory: dict) -> None:
    if not _pool:
        return
    await _pool.execute(
        "UPDATE users SET memory = $2::jsonb WHERE phone = $1",
        phone, json.dumps(memory),
    )


# ── Sessions ─────────────────────────────────────────────────────

async def create_session(session_id: str, user_phone: str) -> None:
    if not _pool:
        return
    await _pool.execute(
        "INSERT INTO wa_sessions (id, user_phone) VALUES ($1, $2)",
        session_id, user_phone,
    )


async def touch_session(session_id: str) -> None:
    if not _pool:
        return
    await _pool.execute(
        "UPDATE wa_sessions SET last_active = NOW() WHERE id = $1",
        session_id,
    )


async def get_latest_session(user_phone: str) -> Optional[asyncpg.Record]:
    if not _pool:
        return None
    return await _pool.fetchrow(
        """SELECT * FROM wa_sessions WHERE user_phone = $1
           ORDER BY last_active DESC LIMIT 1""",
        user_phone,
    )


# ── Messages ─────────────────────────────────────────────────────

async def save_message(
    session_id: str, user_phone: str, role: str, body: str,
) -> None:
    if not _pool:
        return
    await _pool.execute(
        """INSERT INTO messages (session_id, user_phone, role, body)
           VALUES ($1, $2, $3, $4)""",
        session_id, user_phone, role, body,
    )


async def get_messages(session_id: str, limit: int = 50) -> list[asyncpg.Record]:
    if not _pool:
        return []
    return await _pool.fetch(
        """SELECT * FROM messages WHERE session_id = $1
           ORDER BY created_at ASC LIMIT $2""",
        session_id, limit,
    )


# ── Productions ──────────────────────────────────────────────────

async def create_production(
    session_id: str, user_phone: str, block_id: str, topic: str = "",
) -> int:
    if not _pool:
        return 0
    return await _pool.fetchval(
        """INSERT INTO productions (session_id, user_phone, block_id, topic, status)
           VALUES ($1, $2, $3, $4, 'producing')
           RETURNING id""",
        session_id, user_phone, block_id, topic,
    )


async def complete_production(
    production_id: int,
    gcs_url: str = "",
    youtube_id: str = "",
    youtube_url: str = "",
) -> None:
    if not _pool or not production_id:
        return
    await _pool.execute(
        """UPDATE productions
           SET status = 'done', gcs_url = $2, youtube_id = $3,
               youtube_url = $4, completed_at = NOW()
           WHERE id = $1""",
        production_id, gcs_url, youtube_id, youtube_url,
    )


async def fail_production(production_id: int, error: str = "") -> None:
    if not _pool or not production_id:
        return
    await _pool.execute(
        "UPDATE productions SET status = 'error', completed_at = NOW() WHERE id = $1",
        production_id,
    )


async def get_user_productions(
    user_phone: str, limit: int = 20,
) -> list[asyncpg.Record]:
    if not _pool:
        return []
    return await _pool.fetch(
        """SELECT * FROM productions WHERE user_phone = $1
           ORDER BY created_at DESC LIMIT $2""",
        user_phone, limit,
    )


# ── Production Assets ───────────────────────────────────────────

async def save_asset(
    production_id: int,
    asset_type: str,
    source_url: str = "",
    gcs_url: str = "",
    prompt: str = "",
    shot_index: int = 0,
) -> None:
    if not _pool or not production_id:
        return
    await _pool.execute(
        """INSERT INTO production_assets
           (production_id, asset_type, shot_index, source_url, gcs_url, prompt)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        production_id, asset_type, shot_index, source_url, gcs_url, prompt,
    )


async def get_production_assets(
    production_id: int,
) -> list[asyncpg.Record]:
    if not _pool:
        return []
    return await _pool.fetch(
        """SELECT * FROM production_assets WHERE production_id = $1
           ORDER BY asset_type, shot_index""",
        production_id,
    )
