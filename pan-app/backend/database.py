"""Neon-backed, PII-free operational records for registration sessions."""
from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import UUID

from psycopg_pool import ConnectionPool

pool: ConnectionPool | None = None


def is_configured() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def is_ready() -> bool:
    return pool is not None and not pool.closed


def _initialize_sync(database_url: str) -> None:
    global pool
    candidate = ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"autocommit": True},
        timeout=15,
    )
    try:
        candidate.open()
        candidate.wait(timeout=20)
        with candidate.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS veriidia_registration_sessions (
                    id UUID PRIMARY KEY,
                    token_sha256 CHAR(64) NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    closed_at TIMESTAMPTZ,
                    fields_verified SMALLINT NOT NULL DEFAULT 0,
                    provider_http_status SMALLINT
                )
                """
            )
            connection.execute(
                "DELETE FROM veriidia_registration_sessions WHERE created_at < now() - interval '30 days'"
            )
        pool = candidate
    except Exception:
        candidate.close()
        raise


async def initialize(*, required: bool) -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        if required:
            raise RuntimeError("Production requires DATABASE_URL for registration session records.")
        return
    await asyncio.to_thread(_initialize_sync, database_url)


def _record_created_sync(session_id: UUID, digest: str, status: str) -> None:
    if pool is None:
        return
    with pool.connection() as connection:
        connection.execute(
            "INSERT INTO veriidia_registration_sessions (id, token_sha256, status) VALUES (%s, %s, %s)",
            (session_id, digest, status),
        )


async def record_created(session_id: UUID, token: str, status: str) -> None:
    if pool is None:
        return
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    await asyncio.to_thread(_record_created_sync, session_id, digest, status)


def _record_update_sync(
    session_id: UUID,
    status: str,
    fields_verified: int,
    provider_http_status: int | None,
    closed: bool,
) -> None:
    if pool is None:
        return
    with pool.connection() as connection:
        connection.execute(
            """
            UPDATE veriidia_registration_sessions
            SET status = %s,
                updated_at = now(),
                fields_verified = %s,
                provider_http_status = COALESCE(%s, provider_http_status),
                closed_at = CASE WHEN %s THEN now() ELSE closed_at END
            WHERE id = %s
            """,
            (status, fields_verified, provider_http_status, closed, session_id),
        )


async def record_update(
    session_id: UUID | None,
    status: str,
    fields_verified: int,
    *,
    provider_http_status: int | None = None,
    closed: bool = False,
) -> None:
    if pool is None or session_id is None:
        return
    await asyncio.to_thread(
        _record_update_sync,
        session_id,
        status,
        fields_verified,
        provider_http_status,
        closed,
    )


def _close_sync() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


async def close() -> None:
    await asyncio.to_thread(_close_sync)