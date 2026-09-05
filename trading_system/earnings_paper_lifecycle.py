from __future__ import annotations

from enum import StrEnum


class EarningsPaperLifecycleStatus(StrEnum):
    """Canonical externally visible lifecycle states for earnings PAPER execution.

    Values intentionally match the existing API/runtime strings.  This module is
    vocabulary only: introducing it must not change execution authority, state
    persistence, broker behavior, or LIVE behavior.
    """

    WAITING_ANALYSIS = "waiting_analysis"
    WAITING_APPROVAL = "waiting_approval"
    OBSERVING_POST_RELEASE = "observing_post_release"
    WAITING_CONFIRMATION = "waiting_confirmation"
    PAPER_EXECUTED = "paper_executed"
    EXPIRED_NO_TRADE = "expired_no_trade"
    FAILED = "failed"


TERMINAL_EARNINGS_PAPER_STATUSES = frozenset(
    {
        EarningsPaperLifecycleStatus.PAPER_EXECUTED,
        EarningsPaperLifecycleStatus.EXPIRED_NO_TRADE,
        EarningsPaperLifecycleStatus.FAILED,
    }
)

EXECUTION_BLOCKED_EARNINGS_PAPER_STATUSES = frozenset(
    {
        EarningsPaperLifecycleStatus.WAITING_ANALYSIS,
        EarningsPaperLifecycleStatus.WAITING_APPROVAL,
        EarningsPaperLifecycleStatus.OBSERVING_POST_RELEASE,
        EarningsPaperLifecycleStatus.WAITING_CONFIRMATION,
        EarningsPaperLifecycleStatus.EXPIRED_NO_TRADE,
        EarningsPaperLifecycleStatus.FAILED,
    }
)


def is_terminal_earnings_paper_status(
    status: EarningsPaperLifecycleStatus | str,
) -> bool:
    """Return whether ``status`` is a canonical terminal earnings PAPER state."""
    return EarningsPaperLifecycleStatus(status) in TERMINAL_EARNINGS_PAPER_STATUSES


def blocks_earnings_paper_execution(
    status: EarningsPaperLifecycleStatus | str,
) -> bool:
    """Return whether ``status`` must not itself authorize broker execution.

    ``paper_executed`` is the only canonical state that represents execution
    already having occurred.  All pre-execution, expired and failed states remain
    execution-blocking.
    """
    return EarningsPaperLifecycleStatus(status) in EXECUTION_BLOCKED_EARNINGS_PAPER_STATUSES
