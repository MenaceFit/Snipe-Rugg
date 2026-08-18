from __future__ import annotations

from snipe_rugg.dev.models import DevRiskAssessment, Severity
from snipe_rugg.strategy.models import StrategyConfig
from snipe_rugg.strategy.rules import should_enter

CREATOR = "DevWallet111"


def test_enters_when_no_risk_assessment_exists():
    assessment = DevRiskAssessment(creator_address=CREATOR, signals=[], overall_severity=None)
    assert should_enter(assessment, config=StrategyConfig()) is True


def test_enters_when_overall_severity_is_medium():
    assessment = DevRiskAssessment(creator_address=CREATOR, signals=[], overall_severity=Severity.MEDIUM)
    assert should_enter(assessment, config=StrategyConfig()) is True


def test_skips_high_risk_creator_by_default():
    assessment = DevRiskAssessment(creator_address=CREATOR, signals=[], overall_severity=Severity.HIGH)
    assert should_enter(assessment, config=StrategyConfig()) is False


def test_high_risk_gate_can_be_disabled():
    assessment = DevRiskAssessment(creator_address=CREATOR, signals=[], overall_severity=Severity.HIGH)
    config = StrategyConfig(skip_high_risk_creators=False)
    assert should_enter(assessment, config=config) is True
