"""Repeated-behavior pattern detection over a DevProfile (spec section 20-37,
56-59, 84).

Every threshold below is an explicit, named, tunable constant — not a
fabricated "risk score" dressed up as precise. Spec section 62 is the hard
rule this module is built around: never claim single-signal proof of fraud.
`assess_dev` enforces that structurally rather than just in wording:
`DevRiskAssessment.overall_severity` only reaches HIGH once at least two
independent signals corroborate each other (see `_combine`); a lone
HIGH-severity signal is still reported, but only as that one pattern, never
promoted to an overall verdict by itself. Labels use "HIGH-RISK REPEATED
PATTERN" / "REPEATED PATTERN — WATCH" language — never "rugger", "scam", or
"fraud".
"""
from __future__ import annotations

from snipe_rugg.dev.models import DevProfile, DevRiskAssessment, PatternType, RiskSignal, Severity
from snipe_rugg.dev.profile import EARLY_SELL_WINDOW_SECONDS

SERIAL_LAUNCHER_COUNT_MEDIUM = 5
SERIAL_LAUNCHER_COUNT_HIGH = 10
SERIAL_LAUNCHER_WINDOW_SECONDS = 24 * 3600

LOW_GRADUATION_MIN_LAUNCHES = 3
LOW_GRADUATION_RATE_HIGH = 0.0
LOW_GRADUATION_RATE_MEDIUM = 0.15

RAPID_RELAUNCH_MIN_LAUNCHES = 3
RAPID_RELAUNCH_MAX_SECONDS = 600  # 10 minutes

DEV_SOLD_OWN_LAUNCH_MIN_MEDIUM = 1
DEV_SOLD_OWN_LAUNCH_MIN_HIGH = 3


def assess_dev(profile: DevProfile) -> DevRiskAssessment:
    signals = [
        s
        for s in (
            _serial_launcher(profile),
            _low_graduation_rate(profile),
            _rapid_relaunch(profile),
            _dev_sold_own_launch(profile),
        )
        if s is not None
    ]
    return DevRiskAssessment(
        creator_address=profile.creator_address,
        signals=signals,
        overall_severity=_combine(signals),
    )


def _label(severity: Severity) -> str:
    if severity is Severity.HIGH:
        return "HIGH-RISK REPEATED PATTERN"
    if severity is Severity.MEDIUM:
        return "REPEATED PATTERN — WATCH"
    return "PATTERN NOTED"


def _serial_launcher(profile: DevProfile) -> RiskSignal | None:
    if profile.total_launches < SERIAL_LAUNCHER_COUNT_MEDIUM:
        return None
    span_seconds = None
    if profile.first_launch_at is not None and profile.last_launch_at is not None:
        span_seconds = (profile.last_launch_at - profile.first_launch_at).total_seconds()
    if span_seconds is None or span_seconds > SERIAL_LAUNCHER_WINDOW_SECONDS:
        return None
    severity = Severity.HIGH if profile.total_launches >= SERIAL_LAUNCHER_COUNT_HIGH else Severity.MEDIUM
    return RiskSignal(
        pattern_type=PatternType.SERIAL_LAUNCHER,
        severity=severity,
        label=_label(severity),
        description=f"{profile.total_launches} tokens launched within {span_seconds / 3600:.1f}h",
        evidence={"total_launches": profile.total_launches, "span_seconds": span_seconds},
    )


def _low_graduation_rate(profile: DevProfile) -> RiskSignal | None:
    if profile.total_launches < LOW_GRADUATION_MIN_LAUNCHES or profile.graduation_rate is None:
        return None
    if profile.graduation_rate > LOW_GRADUATION_RATE_MEDIUM:
        return None
    severity = Severity.HIGH if profile.graduation_rate <= LOW_GRADUATION_RATE_HIGH else Severity.MEDIUM
    return RiskSignal(
        pattern_type=PatternType.LOW_GRADUATION_RATE,
        severity=severity,
        label=_label(severity),
        description=(
            f"{profile.graduated_count}/{profile.total_launches} launches graduated "
            f"({profile.graduation_rate:.0%})"
        ),
        evidence={
            "total_launches": profile.total_launches,
            "graduated_count": profile.graduated_count,
            "graduation_rate": profile.graduation_rate,
        },
    )


def _rapid_relaunch(profile: DevProfile) -> RiskSignal | None:
    if profile.total_launches < RAPID_RELAUNCH_MIN_LAUNCHES or profile.avg_seconds_between_launches is None:
        return None
    if profile.avg_seconds_between_launches > RAPID_RELAUNCH_MAX_SECONDS:
        return None
    severity = Severity.HIGH if profile.total_launches >= SERIAL_LAUNCHER_COUNT_HIGH else Severity.MEDIUM
    return RiskSignal(
        pattern_type=PatternType.RAPID_RELAUNCH,
        severity=severity,
        label=_label(severity),
        description=(
            f"Average {profile.avg_seconds_between_launches:.0f}s between launches "
            f"across {profile.total_launches} tokens"
        ),
        evidence={
            "avg_seconds_between_launches": profile.avg_seconds_between_launches,
            "total_launches": profile.total_launches,
        },
    )


def _dev_sold_own_launch(profile: DevProfile) -> RiskSignal | None:
    if profile.early_sell_count < DEV_SOLD_OWN_LAUNCH_MIN_MEDIUM:
        return None
    severity = Severity.HIGH if profile.early_sell_count >= DEV_SOLD_OWN_LAUNCH_MIN_HIGH else Severity.MEDIUM
    return RiskSignal(
        pattern_type=PatternType.DEV_SOLD_OWN_LAUNCH,
        severity=severity,
        label=_label(severity),
        description=(
            f"Sold its own launched token within {EARLY_SELL_WINDOW_SECONDS // 60} min of launch, "
            f"{profile.early_sell_count} time(s)"
        ),
        evidence={"early_sell_count": profile.early_sell_count},
    )


def _combine(signals: list[RiskSignal]) -> Severity | None:
    """Spec section 62: never claim single-signal proof of fraud. A lone HIGH
    signal is real information — still returned in `signals` — but is
    deliberately downgraded to an overall MEDIUM here; only when a *second*,
    independent HIGH signal corroborates it does the combined read reach HIGH.
    Any MEDIUM signal (on its own or alongside others) keeps the overall read
    at MEDIUM; a HIGH signal never gets diluted back down to LOW just because
    nothing else corroborates it."""
    if not signals:
        return None
    high_count = sum(1 for s in signals if s.severity is Severity.HIGH)
    if high_count >= 2:
        return Severity.HIGH
    if high_count == 1 or any(s.severity is Severity.MEDIUM for s in signals):
        return Severity.MEDIUM
    return Severity.LOW
