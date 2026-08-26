from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from trading_system.manual_release_ingestion import ManualOfficialReleaseProvider


class ManualReleaseBaselineErrorTests(unittest.TestCase):
    def test_pdf_parent_propagates_baseline_memory_setup_error(self) -> None:
        fake_context = MagicMock()
        recv_conn = MagicMock()
        send_conn = MagicMock()
        fake_context.Pipe.return_value = (recv_conn, send_conn)
        process = MagicMock()
        fake_context.Process.return_value = process
        recv_conn.poll.return_value = True
        recv_conn.recv.return_value = (
            "error",
            "RuntimeError: unable to determine PDF worker baseline memory",
        )

        with patch(
            "trading_system.manual_release_ingestion.multiprocessing.get_context",
            return_value=fake_context,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "unable to determine PDF worker baseline memory",
            ):
                ManualOfficialReleaseProvider._extract_pdf_text(b"%PDF-1.7")


if __name__ == "__main__":
    unittest.main()
