"""The one place WalletTracker (tracking/wallet_tracker.py) and TokenTracker
(tracking/token_tracker.py) call into after new activity touches a creator
address, so profile-recompute + signal-persist + escalation logic lives once,
not duplicated across both callers.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.models import DevProfile, DevRiskAssessment, RiskSignal
from snipe_rugg.dev.patterns import assess_dev
from snipe_rugg.dev.profile import build_profile


class DevMonitorService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_profile(self, creator_address: str) -> DevProfile:
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            tokens = await repo.list_tokens_by_creator(creator_address)
            trades = await repo.list_trades_for_wallet(creator_address)
        return build_profile(creator_address, tokens, trades)

    async def assess(self, creator_address: str) -> DevRiskAssessment:
        return assess_dev(await self.get_profile(creator_address))

    async def refresh(self, creator_address: str) -> tuple[DevProfile, DevRiskAssessment, list[RiskSignal]]:
        """Recompute the profile/assessment and persist it, returning the
        signals that are newly detected or escalated in severity since the
        last refresh — the set that's actually alert-worthy, so callers don't
        re-alert on a standing pattern every time it's re-confirmed."""
        profile = await self.get_profile(creator_address)
        assessment = assess_dev(profile)
        escalated: list[RiskSignal] = []
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            current_patterns = {signal.pattern_type.value for signal in assessment.signals}
            for signal in assessment.signals:
                _, is_escalated = await repo.upsert_dev_risk_signal(
                    creator_address=creator_address,
                    pattern_type=signal.pattern_type.value,
                    severity=signal.severity.value,
                    evidence=signal.evidence,
                )
                if is_escalated:
                    escalated.append(signal)

            # A pattern that no longer holds (e.g. a graduation improved the
            # rate) is dropped rather than left stale, so the stored signal set
            # always matches the most recently computed reality.
            for row in await repo.list_dev_risk_signals(creator_address):
                if row.pattern_type not in current_patterns:
                    await repo.delete_dev_risk_signal(creator_address, row.pattern_type)
            await session.commit()
        return profile, assessment, escalated
