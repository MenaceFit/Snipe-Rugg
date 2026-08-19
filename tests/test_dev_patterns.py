"""assess_dev over hand-built DevProfile objects - no database needed, since
dev/patterns.py operates purely on the profile (see its own docstring). These
tests exist specifically to pin down spec section 62's "never single-signal
proof of fraud" rule: a lone HIGH signal must never reach overall HIGH by
itself (see test_single_high_signal_is_downgraded_to_medium_overall)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from snipe_rugg.dev.models import DevProfile, PatternType, Severity
from snipe_rugg.dev.patterns import assess_dev

CREATOR = "DevWallet111"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _profile(
    *,
    total_launches: int = 1,
    graduated_count: int = 1,
    graduation_rate: float | None = 1.0,
    first_launch_at: datetime | None = T0,
    last_launch_at: datetime | None = T0,
    avg_seconds_between_launches: float | None = None,
    avg_seconds_to_graduation: float | None = 60.0,
    early_sell_count: int = 0,
) -> DevProfile:
    return DevProfile(
        creator_address=CREATOR,
        total_launches=total_launches,
        graduated_count=graduated_count,
        graduation_rate=graduation_rate,
        first_launch_at=first_launch_at,
        last_launch_at=last_launch_at,
        avg_seconds_between_launches=avg_seconds_between_launches,
        avg_seconds_to_graduation=avg_seconds_to_graduation,
        early_sell_count=early_sell_count,
    )


def test_clean_profile_has_no_signals_and_no_overall_severity():
    assessment = assess_dev(_profile())
    assert assessment.signals == []
    assert assessment.overall_severity is None


def test_serial_launcher_fires_at_medium_threshold_within_window():
    profile = _profile(
        total_launches=5,
        graduated_count=5,
        last_launch_at=T0 + timedelta(hours=2),
        avg_seconds_between_launches=1800.0,
    )
    assessment = assess_dev(profile)
    assert [s.pattern_type for s in assessment.signals] == [PatternType.SERIAL_LAUNCHER]
    assert assessment.signals[0].severity is Severity.MEDIUM
    assert assessment.signals[0].label == "REPEATED PATTERN — WATCH"
    assert assessment.overall_severity is Severity.MEDIUM


def test_serial_launcher_requires_the_launches_to_fall_within_the_window():
    profile = _profile(
        total_launches=6,
        graduated_count=6,
        last_launch_at=T0 + timedelta(days=5),
        avg_seconds_between_launches=86400.0,
    )
    assessment = assess_dev(profile)
    assert assessment.signals == []


def test_low_graduation_rate_requires_minimum_launch_count():
    profile = _profile(total_launches=2, graduated_count=0, graduation_rate=0.0)
    assessment = assess_dev(profile)
    assert assessment.signals == []


def test_low_graduation_rate_fires_high_at_zero_graduations():
    profile = _profile(total_launches=4, graduated_count=0, graduation_rate=0.0, avg_seconds_to_graduation=None)
    assessment = assess_dev(profile)
    assert [s.pattern_type for s in assessment.signals] == [PatternType.LOW_GRADUATION_RATE]
    assert assessment.signals[0].severity is Severity.HIGH
    assert assessment.signals[0].label == "HIGH-RISK REPEATED PATTERN"


def test_rapid_relaunch_fires_within_gap_threshold():
    profile = _profile(
        total_launches=3,
        graduated_count=3,
        last_launch_at=T0 + timedelta(seconds=400),
        avg_seconds_between_launches=200.0,
    )
    assessment = assess_dev(profile)
    assert [s.pattern_type for s in assessment.signals] == [PatternType.RAPID_RELAUNCH]
    assert assessment.signals[0].severity is Severity.MEDIUM


def test_dev_sold_own_launch_fires_high_at_three_early_sells():
    profile = _profile(total_launches=1, graduated_count=0, graduation_rate=0.0, early_sell_count=3)
    assessment = assess_dev(profile)
    assert [s.pattern_type for s in assessment.signals] == [PatternType.DEV_SOLD_OWN_LAUNCH]
    assert assessment.signals[0].severity is Severity.HIGH


def test_single_high_signal_is_downgraded_to_medium_overall():
    """The core spec section 62 guarantee: one HIGH-severity pattern is real
    information (still surfaced in `signals`), but never promoted to an
    overall HIGH verdict on its own."""
    profile = _profile(total_launches=1, graduated_count=0, graduation_rate=0.0, early_sell_count=3)
    assessment = assess_dev(profile)
    assert len(assessment.signals) == 1
    assert assessment.signals[0].severity is Severity.HIGH
    assert assessment.overall_severity is Severity.MEDIUM


def test_two_corroborating_high_signals_reach_overall_high():
    profile = _profile(
        total_launches=10,
        graduated_count=0,
        graduation_rate=0.0,
        last_launch_at=T0 + timedelta(hours=2),
        avg_seconds_between_launches=800.0,
        avg_seconds_to_graduation=None,
    )
    assessment = assess_dev(profile)
    pattern_types = {s.pattern_type for s in assessment.signals}
    assert pattern_types == {PatternType.SERIAL_LAUNCHER, PatternType.LOW_GRADUATION_RATE}
    assert all(s.severity is Severity.HIGH for s in assessment.signals)
    assert assessment.overall_severity is Severity.HIGH
