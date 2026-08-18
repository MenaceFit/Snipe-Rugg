"""EventBus topic names published by tracking/* so independent consumers
(the strategy engine) can react without importing or coupling to those
modules' internals — the same decoupling principle as
ingestion/pipeline.py's TOPIC_NORMALIZED_EVENT, just for the business-level
trade events built on top of it.
"""
from __future__ import annotations

TOPIC_NEW_TRADE = "strategy.new_trade"
