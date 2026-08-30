from datetime import datetime

from trading_system.workflow_readiness_evidence_loader import _release_action_timestamp


def test_release_action_timestamp_uses_newest_actionable_evidence() -> None:
    tracked_blocker = {
        "blocker_code": "release_source_missing",
        "message": "Older tracked blocker",
        "updated_at": "2026-08-29T12:00:00+00:00",
    }
    canonical_run = {
        "provider": "canonical_release_worker",
        "status": "error",
        "error_message": "action_required: newer canonical failure",
        "created_at": "2026-08-29T12:10:00+00:00",
    }

    assert _release_action_timestamp(canonical_run, tracked_blocker) == datetime.fromisoformat(
        "2026-08-29T12:10:00+00:00"
    )
