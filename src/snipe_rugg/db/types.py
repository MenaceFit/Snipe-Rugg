"""ExactNumeric: found via Phase 6's strategy-engine tests, which are the
first place in this codebase to do real Decimal arithmetic on values read
back from the database rather than just round-tripping a literal.

Plain SQLAlchemy `Numeric` on SQLite does not preserve exact decimal values:
SQLite has no native fixed-point decimal storage class, so a bound Decimal
ends up stored (and read back) via floating point, and Decimal("0.1") comes
back as Decimal("0.100000000000000006") — the closest IEEE-754 double to 0.1,
re-expressed as a Decimal, not the original 0.1. For a trading system that
computes P&L, this isn't a cosmetic wart; it would corrupt exactly the kind
of number this project promises never to fabricate or silently drift.

ExactNumeric stores the value as TEXT (str(Decimal(...)), an exact,
round-trippable representation) on SQLite, and as the database's own native
NUMERIC on every other backend (postgres, this project's actual production
target per .env.example) — postgres's NUMERIC is already exact arbitrary-
precision decimal, so nothing changes there. Use this instead of
sqlalchemy.Numeric for every monetary/token-amount column.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class ExactNumeric(TypeDecorator):
    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 38, scale: int = 18, **kwargs: Any) -> None:
        self._precision = precision
        self._scale = scale
        super().__init__(**kwargs)

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(self._precision, self._scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "sqlite":
            return str(value)
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if dialect.name == "sqlite":
            return Decimal(value)
        return value


class UTCDateTime(TypeDecorator):
    """Found via Phase 8's live-execution-limit tests, the first code in this
    project to compare a datetime read back from the database against a
    freshly computed `core.clock.utc_now()` value directly (earlier phases
    only ever subtracted two DB-read datetimes from each other, which works
    fine even if both silently lost their tzinfo the same way).

    SQLite has no native timezone-aware timestamp storage: a bound
    tz-aware `datetime` round-trips as a naive one — `datetime.now(UTC)` (
    `tzinfo=UTC`) comes back with `tzinfo=None` — so any later `>=`/`<=`
    comparison against a fresh tz-aware value raises `TypeError: can't
    compare offset-naive and offset-aware datetimes`. Every datetime this
    project produces is UTC (`core/clock.py`'s `utc_now()` is the only
    clock function in the codebase), so on SQLite this reattaches
    `tzinfo=UTC` on the way out rather than leaving it naive. Postgres (this
    project's actual production target) already preserves timezone-aware
    timestamps correctly, so nothing changes there. Use this instead of
    sqlalchemy.DateTime(timezone=True) for every timestamp column.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(DateTime(timezone=False))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if dialect.name == "sqlite" and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
