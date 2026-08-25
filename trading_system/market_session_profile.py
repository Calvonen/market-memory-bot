from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class MarketSessionProfile:
    """Explicit broker-market binding to one exchange session definition.

    Profiles are configuration, not inference. Callers must register only
    market labels that have been grounded from the broker and must supply the
    canonical timezone/calendar identifiers for those labels.

    ``broker_symbol_suffix``/``provider_symbol_suffix`` declare this market's
    symbol policy: the broker and the market-data provider are separate
    namespaces, and a persisted broker instrument is not a valid provider
    ticker. The suffixes are declared configuration for one grounded market -
    not a rule derived from the ticker, country, or calendar - so registering a
    market means asserting how its symbols translate, and any symbol that does
    not carry the declared broker suffix fails closed rather than being guessed.
    """

    etoro_market: str
    market_timezone: str
    calendar_id: str
    broker_symbol_suffix: str
    provider_symbol_suffix: str

    def __post_init__(self) -> None:
        if not self.etoro_market or self.etoro_market != self.etoro_market.strip():
            raise ValueError("etoro_market must be nonblank and trimmed")
        if not self.market_timezone or self.market_timezone != self.market_timezone.strip():
            raise ValueError("market_timezone must be nonblank and trimmed")
        if not self.calendar_id or self.calendar_id != self.calendar_id.strip():
            raise ValueError("calendar_id must be nonblank and trimmed")
        for name, suffix in (
            ("broker_symbol_suffix", self.broker_symbol_suffix),
            ("provider_symbol_suffix", self.provider_symbol_suffix),
        ):
            if not suffix or suffix != suffix.strip():
                raise ValueError(f"{name} must be nonblank and trimmed")
            if not suffix.startswith("."):
                raise ValueError(f"{name} must start with '.'")
            if suffix != suffix.upper():
                raise ValueError(f"{name} must be uppercase")
        try:
            ZoneInfo(self.market_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("market_timezone must be a valid IANA timezone") from exc


# First production-grounded broker market profile. The exact eToro label
# "Sydney" was persisted by the tracked-event preflight for WDS.ASX on
# 2026-08-24. XASX is the Australian Securities Exchange MIC; no aliases or
# ticker/country inference are registered alongside it.
#
# Symbol policy: eToro suffixes this market's instruments ".ASX" (WDS.ASX,
# NHF.ASX), while Yahoo lists the same ASX securities under ".AX" (WDS.AX,
# NHF.AX). The translation is declared here, once, for the whole grounded
# market rather than per instrument, so every Sydney instrument resolves the
# same way - and only symbols actually carrying the ".ASX" broker suffix
# resolve at all.
SYDNEY_MARKET_SESSION_PROFILE = MarketSessionProfile(
    etoro_market="Sydney",
    market_timezone="Australia/Sydney",
    calendar_id="XASX",
    broker_symbol_suffix=".ASX",
    provider_symbol_suffix=".AX",
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


def resolve_provider_symbol(broker_symbol: str, *, profile: MarketSessionProfile) -> str:
    """Translate one exact broker instrument symbol into its provider symbol.

    Broker identity and market-data-provider identity are different namespaces.
    ``resolved_etoro_market`` plus the persisted broker instrument stay the
    authority on *what* is being tracked; this produces only the ticker used to
    ask the data provider about it, and never replaces the broker identity.

    Translation is exact and driven solely by the profile's declared suffixes:
    the symbol must already be in canonical persisted form (uppercase, trimmed)
    and must end with ``profile.broker_symbol_suffix``. Nothing is case-folded,
    aliased, or inferred from the ticker, country, or calendar - a symbol that
    does not carry the declared suffix fails closed here, before any provider
    call, rather than being guessed at.
    """
    if not broker_symbol or broker_symbol != broker_symbol.strip():
        raise ValueError("broker_symbol must be nonblank and trimmed")
    if broker_symbol != broker_symbol.upper():
        raise ValueError(f"broker_symbol must be uppercase: {broker_symbol}")
    if not broker_symbol.endswith(profile.broker_symbol_suffix):
        raise ValueError(
            f"broker symbol {broker_symbol} does not carry the "
            f"{profile.etoro_market} broker suffix {profile.broker_symbol_suffix}"
        )

    base = broker_symbol[: -len(profile.broker_symbol_suffix)]
    if not base or "." in base:
        raise ValueError(f"broker symbol {broker_symbol} has no usable instrument base")
    return f"{base}{profile.provider_symbol_suffix}"


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
