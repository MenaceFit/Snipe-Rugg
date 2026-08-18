"""Edge vocabulary for the wallet relationship graph (spec section 64-69,
122, 144-145). Every edge kind maps directly to something already persisted —
no inferred or fabricated relationship exists here.
"""
from __future__ import annotations

from enum import StrEnum


class EdgeKind(StrEnum):
    FUNDED = "FUNDED"  # SOL transfer, wallet -> wallet
    TRANSFERRED = "TRANSFERRED"  # SPL token transfer, wallet -> wallet
    CREATED = "CREATED"  # creator -> mint (launchpad/detector.py)
    BOUGHT = "BOUGHT"  # wallet -> mint
    SOLD = "SOLD"  # wallet -> mint
