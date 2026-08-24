from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class MarketSessionProfile:
    """Explicit broker-market binding to one exchange session definition.

    Profiles are configuration, not inference. Callers must register only
    market labels that have been grounded from the broker and must supply the
    canonical timezone/calendar identifiers for those labels.
    """

    etoro_market: str
    market_timezone: str
    calendar_id: str

    def __post_init__(self) -> None:
        if not self.etoro_market or self.etoro_market != self.etoro_market.strip():
            raise ValueError("etoro_market must be nonblank and trimmed")
        if not self.market_timezone or self.market_timezone != self.market_timezone.strip():
            raise ValueError("market_timezone must be nonblank and trimmed")
        if not self.calendar_id or self.calendar_id != self.calendar_id.strip():
            raise ValueError("calendar_id must be nonblank and trimmed")
        try:
            ZoneInfo(self.market_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("market_timezone must be a valid IANA timezone") from exc


# First production-grounded broker market profile. The exact eToro label
# "Sydney" was persisted by the tracked-event preflight for WDS.ASX on
# 2026-08-24. XASX is the Australian Securities Exchange MIC; no aliases or
# ticker/country inference are registered alongside it.
SYDNEY_MARKET_SESSION_PROFILE = MarketSessionProfile(
    etoro_market="Sydney",
    market_timezone="Australia/Sydney",
    calendar_id="XASX",
)

GROUNDED_MARKET_SESSION_PROFILES = (SYDNEY_MARKET_SESSION_PROFILE,)


def resolve_market_session_profile(
    etoro_market: str,
    *,
    profiles: tuple[MarketSessionProfile, ...],
) -> MarketSessionProfile:
    """Return the one explicitly registered profile for an exact broker label.

    No aliases, suffix rules, country inference, or fallback mapping are used.
    Unknown and ambiguously registered labels fail closed.
    """
    if not etoro_market or etoro_market != etoro_market.strip():
        raise ValueError("etoro_market must be nonblank and trimmed")

    matches = tuple(profile for profile in profiles if profile.etoro_market == etoro_market)
    if len(matches) != 1:
        if not matches:
            raise ValueError(f"unsupported eToro market: {etoro_market}")
        raise ValueError(f"ambiguous eToro market profile: {etoro_market}")
    return matches[0]


def has_grounded_market_session_profile(
    etoro_market: str | None,
    *,
    profiles: tuple[MarketSessionProfile, ...] = GROUNDED_MARKET_SESSION_PROFILES,
) -> bool:
    """Report whether one grounded profile exists for an exact broker label.

    This is the rollout predicate for features that require a grounded market
    session profile. It answers the same question as
    ``resolve_market_session_profile`` without raising, so callers can branch on
    availability instead of catching a ValueError as control flow - a missing
    profile means "not rolled out here yet", which is not an error condition.

    Matching is exact and identical to ``resolve_market_session_profile``: no
    aliases, suffix rules, ticker/country inference, or fallback mapping. A
    missing, blank, untrimmed, or ambiguously registered label is not grounded.
    """
    if not etoro_market or etoro_market != etoro_market.strip():
        return False
    return len([profile for profile in profiles if profile.etoro_market == etoro_market]) == 1
