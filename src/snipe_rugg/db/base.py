"""Async SQLAlchemy engine/session setup.

Schema uses portable column types (String, Numeric, DateTime, Boolean, JSON) so
the test suite runs against sqlite+aiosqlite without a running Postgres —
production targets postgres+asyncpg per .env.example. Migrations (e.g. Alembic)
aren't wired up yet; init_models() is create_all() convenience for development
and tests, not a substitute for real migrations once this touches a database
with data worth keeping.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_engine(database_url: str, *, echo: bool = False, **kwargs: object) -> AsyncEngine:
    return create_async_engine(database_url, echo=echo, **kwargs)  # type: ignore[arg-type]


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
