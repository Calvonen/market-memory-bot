from __future__ import annotations

import resource
import unittest
from unittest.mock import MagicMock, patch

from trading_system.manual_release_ingestion import (
    ManualOfficialReleaseProvider,
    _pdf_extract_worker,
)


class ManualReleaseMemoryLimitTests(unittest.TestCase):
    def test_pdf_worker_clamps_relative_budget_to_inherited_hard_limit(self) -> None:
        send_conn = MagicMock()
        baseline = 1_500_000_000
        budget = 1_073_741_824
        inherited_hard = baseline + 256 * 1024 * 1024

        with (
            patch(
                "trading_system.manual_release_ingestion._current_virtual_memory_bytes",
                return_value=baseline,
            ),
            patch("resource.getrlimit", return_value=(inherited_hard, inherited_hard)),
            patch("resource.setrlimit") as setrlimit,
            patch.object(
                ManualOfficialReleaseProvider,
                "_extract_pdf_text_in_process",
                return_value="results",
            ),
        ):
            _pdf_extract_worker(b"%PDF-1.7", send_conn, 10, 1000, budget, 5)

        setrlimit.assert_any_call(
            resource.RLIMIT_AS,
            (inherited_hard, inherited_hard),
        )
        send_conn.send.assert_called_once_with(("ok", "results"))

    def test_pdf_worker_uses_relative_budget_when_hard_limit_is_infinite(self) -> None:
        send_conn = MagicMock()
        baseline = 1_500_000_000
        budget = 1_073_741_824
        desired = baseline + budget

        with (
            patch(
                "trading_system.manual_release_ingestion._current_virtual_memory_bytes",
                return_value=baseline,
            ),
            patch(
                "resource.getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            patch("resource.setrlimit") as setrlimit,
            patch.object(
                ManualOfficialReleaseProvider,
                "_extract_pdf_text_in_process",
                return_value="results",
            ),
        ):
            _pdf_extract_worker(b"%PDF-1.7", send_conn, 10, 1000, budget, 5)

        setrlimit.assert_any_call(
            resource.RLIMIT_AS,
            (desired, resource.RLIM_INFINITY),
        )
        send_conn.send.assert_called_once_with(("ok", "results"))


if __name__ == "__main__":
    unittest.main()
