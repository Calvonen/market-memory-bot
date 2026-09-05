from __future__ import annotations

from enum import StrEnum


class EarningsPaperLifecycleStatus(StrEnum):
    """Canonical externally visible lifecycle states for earnings PAPER execution.

    Values intentionally match the existing API/runtime strings. This module is
    vocabulary only: introducing it must not change execution authority, state
    persistence, broker behavior, or LIVE behavior.
    """

    WAITING_ANALYSIS = "waiting_analysis"
    WAITING_APPROVAL = "waiting_approval"
    OBSERVING_POST_RELEASE = "observing_post_release"
    WAITING_CONFIRMATION = "waiting_confirmation"
    PAPER_EXECUTED = "paper_executed"
    EXPIRED_NO_TRADE = "expired_no_trade"


TERMINAL_EARNINGS_PAPER_STATUSES = frozenset(
    {
        EarningsPaperLifecycleStatus.PAPER_EXECUTED,
        EarningsPaperLifecycleStatus.EXPIRED_NO_TRADE,
    }
)

EXECUTION_BLOCKED_EARNINGS_PAPER_STATUSES = frozenset(EarningsPaperLifecycleStatus)


def is_terminal_earnings_paper_status(
    status: EarningsPaperLifecycleStatus | str,
) -> bool:
    """Return whether ``status`` is a canonical terminal earnings PAPER state."""
    return EarningsPaperLifecycleStatus(status) in TERMINAL_EARNINGS_PAPER_STATUSES


def blocks_earnings_paper_execution(
    status: EarningsPaperLifecycleStatus | str,
) -> bool:
    """Return whether an existing lifecycle state blocks another broker execution.

    Every existing lifecycle state is execution-blocking. In particular,
    ``paper_executed`` must block retries from executing again. Broker execution
    authority is established by the existing task/claim/Strategy/Risk/session
    gates, not by treating any persisted lifecycle status as executable.
    Unknown values raise ``ValueError`` and therefore fail closed.
    """
    return EarningsPaperLifecycleStatus(status) in EXECUTION_BLOCKED_EARNINGS_PAPER_STATUSES
