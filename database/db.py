import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def create_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("PostgreSQL pool created")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized. Call create_pool() first.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


async def init_db() -> None:
    """Создать таблицы если не существуют."""
    async with get_pool().acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              BIGINT        PRIMARY KEY,
            username        TEXT,
            full_name       TEXT,
            language        TEXT          NOT NULL DEFAULT 'ru',
            profile_key     TEXT          UNIQUE NOT NULL,
            balance         NUMERIC(12,2) NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            referred_by     BIGINT
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id              BIGSERIAL   PRIMARY KEY,
            user_id         BIGINT      NOT NULL REFERENCES users(id),
            xui_client_id   TEXT,
            xui_email       TEXT,
            sub_link        TEXT,
            plan            TEXT,
            devices_limit   INTEGER     NOT NULL DEFAULT 3,
            started_at      TIMESTAMPTZ,
            expires_at      TIMESTAMPTZ,
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE
        );

        CREATE INDEX IF NOT EXISTS idx_subscriptions_user_active
            ON subscriptions(user_id, is_active);

        CREATE TABLE IF NOT EXISTS payments (
            id              BIGSERIAL     PRIMARY KEY,
            user_id         BIGINT        NOT NULL REFERENCES users(id),
            amount          NUMERIC(12,2),
            currency        TEXT          NOT NULL DEFAULT 'RUB',
            provider        TEXT,
            provider_id     TEXT          UNIQUE,
            purpose         TEXT,
            status          TEXT          NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            paid_at         TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_payments_provider_id
            ON payments(provider_id);
                           
        CREATE TABLE IF NOT EXISTS expiry_notifications (
            id          BIGSERIAL   PRIMARY KEY,
            sub_id      BIGINT      NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
            notify_days INTEGER     NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (sub_id, notify_days)
        );
        """)
    logger.info("Database schema initialized")


def _row(record) -> dict | None:
    return dict(record) if record else None


async def get_user(user_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE id = $1", user_id
        )
        return _row(row)


async def get_user_by_profile_key(profile_key: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE profile_key = $1", profile_key
        )
        return _row(row)


async def create_user(user_id: int, username: str, full_name: str,
                      language: str, profile_key: str,
                      referred_by: int = None) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("""
            INSERT INTO users (id, username, full_name, language, profile_key, referred_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
        """, user_id, username, full_name, language, profile_key, referred_by)


async def update_user_language(user_id: int, language: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET language = $1 WHERE id = $2", language, user_id
        )


async def update_balance(user_id: int, delta: float) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET balance = balance + $1 WHERE id = $2", delta, user_id
        )


async def get_balance(user_id: int) -> float:
    async with get_pool().acquire() as conn:
        val = await conn.fetchval(
            "SELECT balance FROM users WHERE id = $1", user_id
        )
        return float(val) if val is not None else 0.0


async def count_referrals(user_id: int) -> int:
    """Количество пользователей, которых пригласил данный юзер."""
    async with get_pool().acquire() as conn:
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE referred_by = $1", user_id
        )
        return int(val) if val else 0


async def get_active_subscription(user_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM subscriptions
            WHERE user_id = $1 AND is_active = TRUE
            ORDER BY expires_at DESC
            LIMIT 1
        """, user_id)
        return _row(row)


async def create_subscription(user_id: int, xui_client_id: str, xui_email: str,
                               sub_link: str, plan: str, days: int,
                               devices_limit: int = 3) -> int:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days)
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO subscriptions
                (user_id, xui_client_id, xui_email, sub_link, plan,
                 devices_limit, started_at, expires_at, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE)
            RETURNING id
        """, user_id, xui_client_id, xui_email, sub_link, plan,
             devices_limit, now, expires)
        return row["id"]


async def extend_subscription(sub_id: int, extra_days: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("""
            UPDATE subscriptions
            SET expires_at = expires_at + ($1 || ' days')::INTERVAL
            WHERE id = $2
        """, str(extra_days), sub_id)


async def update_devices_limit(sub_id: int, new_limit: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET devices_limit = $1 WHERE id = $2",
            new_limit, sub_id
        )


async def deactivate_subscription(sub_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET is_active = FALSE WHERE id = $1", sub_id
        )


async def create_payment(user_id: int, amount: float, currency: str,
                          provider: str, provider_id: str, purpose: str) -> int:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO payments (user_id, amount, currency, provider, provider_id, purpose)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, user_id, amount, currency, provider, provider_id, purpose)
        return row["id"]


async def get_payment_by_provider_id(provider_id: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE provider_id = $1", provider_id
        )
        return _row(row)


async def mark_payment_paid(provider_id: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("""
            UPDATE payments
            SET status = 'paid', paid_at = NOW()
            WHERE provider_id = $1
        """, provider_id)